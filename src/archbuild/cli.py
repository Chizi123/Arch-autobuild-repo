"""Command-line interface using Click."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from archbuild import __version__
from archbuild.aur import AURClient
from archbuild.builder import Builder, BuildStatus
from archbuild.config import Config, load_config, migrate_vars_sh, save_config
from archbuild.logging import console as log_console, setup_logging
from archbuild.notifications import NotificationManager
from archbuild.repo import RepoManager

console = Console()


def run_async(coro: Any) -> Any:
    """Run async function in sync context."""
    return asyncio.get_event_loop().run_until_complete(coro)


class Context:
    """CLI context holding shared state."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._config: Config | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(self.config_path)
            setup_logging(self._config.log_level, self._config.log_file)
        return self._config


pass_context = click.make_pass_decorator(Context)


@click.group()
@click.option(
    "-c", "--config",
    type=click.Path(exists=False, path_type=Path),
    default=Path("config.yaml"),
    help="Path to configuration file",
)
@click.version_option(__version__, prog_name="archbuild")
@click.pass_context
def cli(ctx: click.Context, config: Path) -> None:
    """Archbuild - Automatic AUR package building and repository management.

    A modern, sustainable replacement for legacy Bash-based AUR build systems.
    """
    ctx.obj = Context(config)


@cli.command()
@click.option("--force", "-f", is_flag=True, help="Force rebuild all packages")
@pass_context
def build_all(ctx: Context, force: bool) -> None:
    """Build all packages in the build directory."""
    config = ctx.config

    async def _build_all() -> None:
        async with AURClient() as aur:
            async with Builder(config, aur) as builder:
                results = await builder.build_all(force=force)

                # Add to repository
                repo = RepoManager(config)
                for result in results:
                    if result.status == BuildStatus.SUCCESS:
                        repo.add_packages(result)

                # Send notifications
                notifier = NotificationManager(config)
                await notifier.notify(results)

                # Print summary
                _print_results(results)

    run_async(_build_all())


@cli.command()
@click.argument("package")
@click.option("--force", "-f", is_flag=True, help="Force rebuild package")
@pass_context
def build(ctx: Context, package: str, force: bool) -> None:
    """Build a specific package in the build directory."""
    config = ctx.config

    async def _build() -> None:
        async with AURClient() as aur:
            async with Builder(config, aur) as builder:
                result = await builder.build_package(package, force=force)

                if result.status == BuildStatus.SUCCESS:
                    repo = RepoManager(config)
                    repo.add_packages(result)

                # Send notifications
                notifier = NotificationManager(config)
                await notifier.notify([result])

                # Print summary
                _print_results([result])

    run_async(_build())


@cli.command()
@click.argument("packages", nargs=-1, required=True)
@pass_context
def add(ctx: Context, packages: tuple[str, ...]) -> None:
    """Add and build new packages from the AUR."""
    config = ctx.config

    async def _add() -> None:
        async with AURClient() as aur:
            async with Builder(config, aur) as builder:
                repo = RepoManager(config)

                results = []
                for package in packages:
                    console.print(f"[bold blue]Adding package:[/] {package}")
                    result = await builder.add_package(package)
                    results.append(result)

                    if result.status == BuildStatus.SUCCESS:
                        repo.add_packages(result)
                        console.print(f"[green]✓[/] {package} added successfully")
                    else:
                        console.print(f"[red]✗[/] {package} failed: {result.error}")

                _print_results(results)

    run_async(_add())


@cli.command()
@click.argument("packages", nargs=-1, required=True)
@click.option("--all-official", "-a", is_flag=True, help="Remove packages that moved to official repos")
@pass_context
def remove(ctx: Context, packages: tuple[str, ...], all_official: bool) -> None:
    """Remove packages from the repository and build directory."""
    config = ctx.config

    async def _remove() -> None:
        async with AURClient() as aur:
            async with Builder(config, aur) as builder:
                repo = RepoManager(config)

                if all_official:
                    # Find packages now in official repos
                    from archbuild.resolver import DependencyResolver
                    resolver = DependencyResolver(aur)

                    for pkg in repo.list_packages():
                        if resolver.is_in_official_repos(pkg.name):
                            console.print(f"[yellow]Removing {pkg.name}[/] (now in official repos)")
                            builder.remove_package(pkg.name)
                            repo.remove_package(pkg.name)
                else:
                    for package in packages:
                        builder.remove_package(package)
                        repo.remove_package(package)
                        console.print(f"[green]✓[/] Removed {package}")

    run_async(_remove())


@cli.command()
@pass_context
def check(ctx: Context) -> None:
    """Check for packages moved to official repos or removed from AUR."""
    config = ctx.config

    async def _check() -> None:
        async with AURClient() as aur:
            from archbuild.resolver import DependencyResolver
            resolver = DependencyResolver(aur)
            repo = RepoManager(config)

            packages = repo.list_packages()
            in_official: list[str] = []
            not_in_aur: list[str] = []

            with console.status("Checking packages..."):
                for pkg in packages:
                    if resolver.is_in_official_repos(pkg.name):
                        in_official.append(pkg.name)
                    elif not await aur.is_available(pkg.name):
                        not_in_aur.append(pkg.name)

            if in_official:
                console.print("\n[yellow]Packages now in official repos:[/]")
                for pkg in in_official:
                    console.print(f"  • {pkg}")

            if not_in_aur:
                console.print("\n[red]Packages not found in AUR:[/]")
                for pkg in not_in_aur:
                    console.print(f"  • {pkg}")

            if not in_official and not not_in_aur:
                console.print("[green]All packages OK[/]")

    run_async(_check())


@cli.command()
@pass_context
def remake(ctx: Context) -> None:
    """Rebuild the repository database from scratch."""
    config = ctx.config
    repo = RepoManager(config)

    if repo.rebuild_database():
        console.print("[green]Repository database rebuilt successfully[/]")
    else:
        console.print("[red]Failed to rebuild repository database[/]")
        sys.exit(1)


@cli.command()
@pass_context
def cleanup(ctx: Context) -> None:
    """Clean up old package versions based on retention settings."""
    config = ctx.config
    repo = RepoManager(config)

    removed = repo.cleanup()
    console.print(f"[green]Removed {removed} old package version(s)[/]")


@cli.command("list")
@pass_context
def list_packages(ctx: Context) -> None:
    """List all packages in the repository."""
    config = ctx.config
    repo = RepoManager(config)

    packages = repo.list_packages()

    if not packages:
        console.print("[yellow]No packages in repository[/]")
        return

    table = Table(title=f"Packages in {config.repository.name}")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="dim")

    for pkg in packages:
        size_mb = pkg.size / (1024 * 1024)
        table.add_row(
            pkg.name,
            pkg.version,
            f"{size_mb:.1f} MB",
            pkg.modified.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@cli.command()
@pass_context
def test_notifications(ctx: Context) -> None:
    """Test notification configuration by sending test messages."""
    config = ctx.config

    async def _test() -> None:
        notifier = NotificationManager(config)
        results = await notifier.test()

        for backend, success in results.items():
            if success:
                console.print(f"[green]✓[/] {backend}: OK")
            else:
                console.print(f"[red]✗[/] {backend}: Failed")

    run_async(_test())


@cli.command()
@click.argument("vars_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output",
    type=click.Path(path_type=Path),
    default=Path("config.yaml"),
    help="Output config file path",
)
def migrate_config(vars_file: Path, output: Path) -> None:
    """Migrate legacy vars.sh to new YAML config format."""
    console.print(f"[blue]Migrating {vars_file} to {output}...[/]")

    try:
        data = migrate_vars_sh(vars_file)
        config = Config.model_validate(data)
        save_config(config, output)
        console.print(f"[green]✓[/] Configuration saved to {output}")
        console.print("[yellow]Note:[/] Please review and update the generated config.")
    except Exception as e:
        console.print(f"[red]Migration failed:[/] {e}")
        sys.exit(1)


@cli.command()
@pass_context
def init(ctx: Context) -> None:
    """Initialize repository directories and configuration."""
    config = ctx.config

    # Create directories
    config.repository.path.mkdir(parents=True, exist_ok=True)
    config.repository.build_dir.mkdir(parents=True, exist_ok=True)

    repo = RepoManager(config)
    repo.ensure_repo_exists()

    console.print(f"[green]✓[/] Repository initialized at {config.repository.path}")
    console.print(f"[green]✓[/] Build directory: {config.repository.build_dir}")

    # Check pacman.conf
    pacman_conf = Path("/etc/pacman.conf")
    if pacman_conf.exists():
        content = pacman_conf.read_text()
        if config.repository.name not in content:
            console.print(
                f"\n[yellow]Note:[/] Add this to /etc/pacman.conf:\n"
                f"[{config.repository.name}]\n"
                f"SigLevel = Optional TrustAll\n"
                f"Server = file://{config.repository.path}"
            )


def _print_results(results: list[Any]) -> None:
    """Print build results summary table."""
    if not results:
        return

    table = Table(title="Build Results")
    table.add_column("Package", style="cyan")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Error", style="dim", max_width=40)

    for result in results:
        status_style = {
            BuildStatus.SUCCESS: "[green]✓ Success[/]",
            BuildStatus.FAILED: "[red]✗ Failed[/]",
            BuildStatus.SKIPPED: "[yellow]⏭ Skipped[/]",
            BuildStatus.PENDING: "[blue]⏳ Pending[/]",
            BuildStatus.BUILDING: "[blue]⚙ Building[/]",
        }

        table.add_row(
            result.package,
            status_style.get(result.status, str(result.status)),
            f"{result.duration:.1f}s",
            (result.error or "")[:40] if result.error else "",
        )

    console.print(table)


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
