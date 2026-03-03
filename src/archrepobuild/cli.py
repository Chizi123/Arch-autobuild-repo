"""Command-line interface using Click."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from archrepobuild import __version__
from archrepobuild.aur import AURClient
from archrepobuild.builder import Builder, BuildStatus
from archrepobuild.config import Config, load_config, migrate_vars_sh, save_config
from archrepobuild.logging import console as log_console, setup_logging
from archrepobuild.notifications import NotificationManager
from archrepobuild.repo import RepoManager

console = Console()


def run_async(coro: Any) -> Any:
    """Run async function in sync context."""
    return asyncio.run(coro)


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
@click.version_option(__version__, prog_name="archrepobuild")
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
@click.option(
    "--include-repo",
    is_flag=True,
    default=False,
    help="Check managed repository for existing packages (skip if present)",
)
@pass_context
def add(ctx: Context, packages: tuple[str, ...], include_repo: bool) -> None:
    """Add and build new packages from the AUR."""
    config = ctx.config

    async def _add() -> None:
        async with AURClient() as aur:
            repo = RepoManager(config)
            async with Builder(config, aur, repo=repo) as builder:
                results = []
                for package in packages:
                    console.print(f"[bold blue]Adding package:[/] {package}")
                    result = await builder.add_package(package, include_repo=include_repo)
                    results.append(result)

                    if result.status == BuildStatus.SUCCESS:
                        if len(result.artifacts) > 1:
                            console.print(f"[green]✓[/] {package} processed successfully ({len(result.artifacts)} artifacts registered)")
                        else:
                            console.print(f"[green]✓[/] {package} processed successfully")
                    elif result.status == BuildStatus.SKIPPED:
                        console.print(f"[yellow]⏭[/] {package} skipped (already in managed repository)")
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
                    from archrepobuild.resolver import DependencyResolver
                    resolver = DependencyResolver(aur)

                    for pkg in repo.list_packages():
                        if resolver.is_in_repos(pkg.name):
                            console.print(f"[yellow]Removing {pkg.name}[/] (now in repositories)")
                            builder.remove_package(pkg.name)
                            repo.remove_package(pkg.name)
                else:
                    for package in packages:
                        builder.remove_package(package)
                        repo.remove_package(package)
                        console.print(f"[green]✓[/] Removed {package}")

    run_async(_remove())


@cli.command()
@click.option("--all-repos", "-a", is_flag=True, help="Include all enabled repositories, not just official ones")
@pass_context
def check(ctx: Context, all_repos: bool) -> None:
    """Check for packages moved to official repos or removed from AUR."""
    config = ctx.config

    async def _check() -> None:
        async with AURClient() as aur:
            from archrepobuild.resolver import DependencyResolver
            resolver = DependencyResolver(aur)
            repo = RepoManager(config)

            packages = repo.list_packages()
            in_official: list[str] = []
            not_in_aur: list[str] = []

            with console.status("Checking packages..."):
                for pkg in packages:
                    # Ignore debug packages if the regular version is in official repos
                    if pkg.name.endswith("-debug"):
                        base_name = pkg.name[:-6]
                        if resolver.is_in_repos(base_name, include_all=all_repos) or await aur.is_available(base_name):
                            continue

                    if resolver.is_in_repos(pkg.name, include_all=all_repos):
                        in_official.append(pkg.name)
                    elif not await aur.is_available(pkg.name):
                        not_in_aur.append(pkg.name)

            if in_official:
                repo_type = "official" if not all_repos else "enabled"
                console.print(f"\n[yellow]Packages now in {repo_type} repos:[/]")
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
@click.option("--systemd", is_flag=True, help="Set up systemd service and timer for automated builds")
@click.option("--gpg", is_flag=True, help="Set up GPG signing for the repository")
@pass_context
def init(ctx: Context, systemd: bool, gpg: bool) -> None:
    """Initialize repository directories and configuration.
    
    This command is idempotent and can be run multiple times to add features
    like systemd automation or GPG signing.
    """
    config = ctx.config

    # Create directories
    config.repository.path.mkdir(parents=True, exist_ok=True)
    config.repository.build_dir.mkdir(parents=True, exist_ok=True)

    repo = RepoManager(config)
    repo.ensure_repo_exists()

    console.print(f"[green]✓[/] Repository directory: {config.repository.path}")
    console.print(f"[green]✓[/] Build directory: {config.repository.build_dir}")

    if systemd:
        _setup_systemd(ctx)
    
    if gpg:
        _setup_gpg(ctx)

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


def _setup_systemd(ctx: Context) -> None:
    """Helper to set up systemd service and timer."""
    import subprocess
    
    console.print("\n[bold blue]═══ Systemd Setup ═══[/]")
    
    interval = click.prompt("How often should builds run? (systemd Calendar spec, e.g., 12h, daily)", default="12h")
    
    # Get absolute path to archrepobuild executable
    import shutil
    archrepobuild_path = shutil.which("archrepobuild")
    if not archrepobuild_path:
        # Fallback to current sys.executable if running as module or in venv
        archrepobuild_path = f"{sys.executable} -m archrepobuild.cli"
    
    user_systemd_dir = Path.home() / ".config" / "systemd" / "user"
    user_systemd_dir.mkdir(parents=True, exist_ok=True)
    
    service_content = f"""[Unit]
Description=Archbuild - Automatic AUR Package Builder
After=network.target

[Service]
Type=oneshot
ExecStart={archrepobuild_path} build-all
Environment="PATH={Path.home()}/.local/bin:/usr/bin:/bin"
"""
    
    timer_content = f"""[Unit]
Description=Timer for Archbuild Automatic Builds

[Timer]
OnCalendar={interval}
Persistent=true

[Install]
WantedBy=timers.target
"""
    
    service_file = user_systemd_dir / "archrepobuild.service"
    timer_file = user_systemd_dir / "archrepobuild.timer"
    
    service_file.write_text(service_content)
    timer_file.write_text(timer_content)
    
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "archrepobuild.timer"], check=True)
        console.print(f"[green]✓[/] Systemd timer enabled (running every {interval})")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/] Failed to enable systemd timer: {e}")


def _setup_gpg(ctx: Context) -> None:
    """Helper to set up GPG signing."""
    import subprocess
    
    console.print("\n[bold blue]═══ GPG Signing Setup ═══[/]")
    
    config = ctx.config
    
    # Check for existing keys
    try:
        result = subprocess.run(
            ["gpg", "--list-secret-keys", "--keyid-format", "LONG"],
            capture_output=True, text=True, check=True
        )
        keys = []
        for line in result.stdout.splitlines():
            if line.startswith("sec"):
                parts = line.split()
                if len(parts) >= 2:
                    key_id = parts[1].split("/")[-1]
                    keys.append(key_id)
        
        if keys:
            console.print("Found existing GPG keys:")
            for i, key in enumerate(keys):
                console.print(f"  [{i}] {key}")
            
            choice = click.prompt(
                "Select a key index, enter a Key ID manually, or type 'new' to generate",
                default="0"
            )
            
            if choice.lower() == "new":
                key_id = _generate_gpg_key()
            elif choice.isdigit() and int(choice) < len(keys):
                key_id = keys[int(choice)]
            else:
                key_id = choice
        else:
            if click.confirm("No secret keys found. Generate a new one?"):
                key_id = _generate_gpg_key()
            else:
                key_id = click.prompt("Enter Key ID manually")
                
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[yellow]GPG not found or failed to list keys.[/]")
        key_id = click.prompt("Enter Key ID manually")

    if key_id:
        config.signing.enabled = True
        config.signing.key = key_id
        save_config(config, ctx.config_path)
        console.print(f"[green]✓[/] Signing enabled with key: {key_id}")
        console.print(f"[green]✓[/] Configuration updated: {ctx.config_path}")


def _generate_gpg_key() -> str:
    """Generate a new GPG key and return its ID."""
    import subprocess
    import tempfile
    
    console.print("Generating new GPG key (this may take a while)...")
    
    name = click.prompt("Name for GPG key", default="Archbuild Repo")
    email = click.prompt("Email for GPG key")
    
    batch_content = f"""
        Key-Type: RSA
        Key-Length: 4096
        Subkey-Type: RSA
        Subkey-Length: 4096
        Name-Real: {name}
        Name-Email: {email}
        Expire-Date: 0
        %no-protection
        %commit
    """
    
    with tempfile.NamedTemporaryFile(mode="w") as f:
        f.write(batch_content)
        f.flush()
        try:
            subprocess.run(["gpg", "--batch", "--generate-key", f.name], check=True)
            
            # Get the ID of the key we just created
            result = subprocess.run(
                ["gpg", "--list-secret-keys", "--keyid-format", "LONG", email],
                capture_output=True, text=True, check=True
            )
            for line in result.stdout.splitlines():
                if line.startswith("sec"):
                    return line.split()[1].split("/")[-1]
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/] Failed to generate GPG key: {e}")
            return ""
    return ""


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
