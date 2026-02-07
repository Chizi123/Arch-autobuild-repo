#!/usr/bin/env python3
"""
Integration test script for archbuild.

This script creates a temporary repository, initializes it with a basic config,
adds test packages, and verifies they build and are added correctly.

Usage:
    python tests/integration_test.py [--keep-temp]
    
Options:
    --keep-temp    Don't delete temporary directory after test (for debugging)
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from archbuild.aur import AURClient
from archbuild.builder import Builder, BuildStatus
from archbuild.config import Config, RepositoryConfig, BuildingConfig, SigningConfig, PackageOverride
from archbuild.logging import setup_logging, console
from archbuild.repo import RepoManager


# Test packages - real packages that exist in the AUR
# Chosen for small size and fast build times
TEST_PACKAGES = [
    "neofetch-git",  # Small bash script, very fast build
    "yay",           # Popular AUR helper
]

# Alternative packages if the above aren't available
FALLBACK_PACKAGES = [
    "gtk2",          # Legacy GTK library
    "paru",          # Another AUR helper
]


class IntegrationTest:
    """Integration test runner."""

    def __init__(self, keep_temp: bool = False):
        self.keep_temp = keep_temp
        self.temp_dir: Path | None = None
        self.config: Config | None = None
        self.passed = 0
        self.failed = 0

    def setup(self) -> None:
        """Set up temporary test environment."""
        console.print("\n[bold blue]═══ Setting up test environment ═══[/]")
        
        # Create temp directory
        self.temp_dir = Path(tempfile.mkdtemp(prefix="archbuild_test_"))
        console.print(f"  Created temp directory: {self.temp_dir}")

        # Create subdirectories
        repo_dir = self.temp_dir / "repo"
        build_dir = self.temp_dir / "build"
        repo_dir.mkdir()
        build_dir.mkdir()

        # Create custom makepkg.conf that disables debug packages 
        # (avoids needing debugedit which may not be installed)
        makepkg_conf = self.temp_dir / "makepkg.conf"
        makepkg_conf.write_text("""
# Minimal makepkg.conf for testing
CARCH="x86_64"
CHOST="x86_64-pc-linux-gnu"
CFLAGS="-O2 -pipe"
CXXFLAGS="$CFLAGS"
LDFLAGS=""
MAKEFLAGS="-j$(nproc)"
OPTIONS=(!debug !strip !staticlibs)
PKGEXT='.pkg.tar.zst'
SRCEXT='.src.tar.gz'
PACKAGER="Integration Test <test@test.local>"

DLAGENTS=('file::/usr/bin/curl -qgC - -o %o %u'
          'ftp::/usr/bin/curl -qgfC - --ftp-pasv --retry 3 --retry-delay 3 -o %o %u'
          'http::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'https::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'rsync::/usr/bin/rsync --no-motd -z %u %o'
          'scp::/usr/bin/scp -C %u %o')

VCSCLIENTS=('git::git'
            'hg::mercurial'
            'svn::subversion')
""")

        self.config = Config(
            repository=RepositoryConfig(
                name="testrepo",
                path=repo_dir,
                build_dir=build_dir,
                compression="zst",
            ),
            building=BuildingConfig(
                parallel=False,  # Sequential for cleaner test output
                max_workers=1,
                clean=True,
                update_system=False,
                retry_attempts=2,
            ),
            signing=SigningConfig(
                enabled=False,
            ),
            log_level="INFO",
            # Apply package overrides to use our custom makepkg.conf
            package_overrides={
                "_default": PackageOverride(
                    extra_args=["--config", str(makepkg_conf)],
                ),
            },
        )

        setup_logging(self.config.log_level)
        console.print("  [green]✓[/] Configuration created")

    def teardown(self) -> None:
        """Clean up test environment."""
        if self.temp_dir and self.temp_dir.exists():
            if self.keep_temp:
                console.print(f"\n[yellow]Keeping temp directory:[/] {self.temp_dir}")
            else:
                shutil.rmtree(self.temp_dir)
                console.print("\n[dim]Cleaned up temp directory[/]")

    def check(self, condition: bool, message: str) -> bool:
        """Check a condition and report pass/fail."""
        if condition:
            console.print(f"  [green]✓[/] {message}")
            self.passed += 1
            return True
        else:
            console.print(f"  [red]✗[/] {message}")
            self.failed += 1
            return False

    async def test_init(self) -> bool:
        """Test repository initialization."""
        console.print("\n[bold blue]═══ Test: Repository Initialization ═══[/]")

        repo = RepoManager(self.config)
        repo.ensure_repo_exists()

        # Check directories exist
        self.check(
            self.config.repository.path.exists(),
            f"Repository directory exists: {self.config.repository.path}"
        )
        self.check(
            self.config.repository.build_dir.exists(),
            f"Build directory exists: {self.config.repository.build_dir}"
        )

        return self.failed == 0

    async def test_aur_client(self) -> list[str]:
        """Test AUR client and find available test packages."""
        console.print("\n[bold blue]═══ Test: AUR Client ═══[/]")

        available_packages = []

        async with AURClient() as aur:
            # Test package lookup
            for pkg_name in TEST_PACKAGES + FALLBACK_PACKAGES:
                pkg = await aur.get_package(pkg_name)
                if pkg:
                    self.check(True, f"Found package: {pkg_name} ({pkg.version})")
                    available_packages.append(pkg_name)
                    if len(available_packages) >= 2:
                        break
                else:
                    console.print(f"  [yellow]⚠[/] Package not found: {pkg_name}")

        self.check(
            len(available_packages) >= 1,
            f"Found {len(available_packages)} test package(s)"
        )

        return available_packages

    async def test_build_packages(self, packages: list[str]) -> dict[str, BuildStatus]:
        """Test building packages."""
        console.print("\n[bold blue]═══ Test: Package Building ═══[/]")

        results: dict[str, BuildStatus] = {}

        async with AURClient() as aur:
            async with Builder(self.config, aur) as builder:
                for pkg_name in packages:
                    console.print(f"\n  Building {pkg_name}...")
                    result = await builder.build_package(pkg_name, force=True)
                    results[pkg_name] = result.status

                    if result.status == BuildStatus.SUCCESS:
                        self.check(True, f"Built {pkg_name} successfully ({result.duration:.1f}s)")
                        self.check(
                            len(result.artifacts) > 0,
                            f"  Created {len(result.artifacts)} artifact(s)"
                        )
                        for artifact in result.artifacts:
                            console.print(f"    → {artifact.name}")
                    else:
                        self.check(False, f"Failed to build {pkg_name}: {result.error}")

        return results

    async def test_repo_add(self, packages: list[str]) -> None:
        """Test adding packages to repository."""
        console.print("\n[bold blue]═══ Test: Repository Management ═══[/]")

        repo = RepoManager(self.config)

        async with AURClient() as aur:
            async with Builder(self.config, aur) as builder:
                for pkg_name in packages:
                    # Get the build result with artifacts
                    pkg_dir = self.config.repository.build_dir / pkg_name
                    if not pkg_dir.exists():
                        continue

                    # Find artifacts
                    artifacts = list(pkg_dir.glob("*.pkg.tar.*"))
                    artifacts = [a for a in artifacts if not a.name.endswith(".sig")]

                    if artifacts:
                        # Create a mock build result for add_packages
                        from archbuild.builder import BuildResult
                        mock_result = BuildResult(
                            package=pkg_name,
                            status=BuildStatus.SUCCESS,
                            artifacts=artifacts,
                        )
                        success = repo.add_packages(mock_result)
                        self.check(success, f"Added {pkg_name} to repository")

        # List packages in repo
        pkg_list = repo.list_packages()
        self.check(len(pkg_list) > 0, f"Repository contains {len(pkg_list)} package(s)")

        for pkg in pkg_list:
            console.print(f"    → {pkg.name} {pkg.version} ({pkg.size / 1024:.1f} KB)")

    async def test_repo_database(self) -> None:
        """Test repository database integrity."""
        console.print("\n[bold blue]═══ Test: Database Integrity ═══[/]")

        db_path = self.config.repository.path / f"{self.config.repository.name}.db.tar.zst"
        
        self.check(db_path.exists(), f"Database file exists: {db_path.name}")

        if db_path.exists():
            # Try to list contents with tar
            result = subprocess.run(
                ["tar", "-tf", str(db_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                entries = [e for e in result.stdout.strip().split("\n") if e]
                self.check(
                    len(entries) > 0,
                    f"Database contains {len(entries)} entries"
                )

        # Check for integrity issues
        repo = RepoManager(self.config)
        issues = repo.check_integrity()
        self.check(
            len(issues) == 0,
            f"No integrity issues found" if not issues else f"Issues: {issues}"
        )

    async def test_cleanup(self) -> None:
        """Test package cleanup functionality."""
        console.print("\n[bold blue]═══ Test: Cleanup ═══[/]")

        repo = RepoManager(self.config)
        removed = repo.cleanup()
        
        self.check(True, f"Cleanup removed {removed} old version(s)")

    async def run(self) -> bool:
        """Run all integration tests."""
        console.print("[bold magenta]╔════════════════════════════════════════════╗[/]")
        console.print("[bold magenta]║     Archbuild Integration Test Suite       ║[/]")
        console.print("[bold magenta]╚════════════════════════════════════════════╝[/]")

        try:
            self.setup()

            # Run tests
            await self.test_init()

            packages = await self.test_aur_client()
            if not packages:
                console.print("[red]No test packages available, cannot continue[/]")
                return False

            build_results = await self.test_build_packages(packages)  # Build all found packages
            
            successful = [p for p, s in build_results.items() if s == BuildStatus.SUCCESS]
            if successful:
                await self.test_repo_add(successful)
                await self.test_repo_database()
                await self.test_cleanup()
            else:
                console.print("[yellow]No successful builds to test repository with[/]")

            # Summary
            console.print("\n[bold blue]═══ Test Summary ═══[/]")
            total = self.passed + self.failed
            console.print(f"  Total:  {total}")
            console.print(f"  [green]Passed: {self.passed}[/]")
            console.print(f"  [red]Failed: {self.failed}[/]")

            if self.failed == 0:
                console.print("\n[bold green]✓ All tests passed![/]")
                return True
            else:
                console.print(f"\n[bold red]✗ {self.failed} test(s) failed[/]")
                return False

        except KeyboardInterrupt:
            console.print("\n[yellow]Test interrupted[/]")
            return False
        except Exception as e:
            console.print(f"\n[bold red]Test error:[/] {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.teardown()


async def main() -> int:
    """Main entry point."""
    keep_temp = "--keep-temp" in sys.argv

    # Check if running on Arch Linux
    if not Path("/etc/arch-release").exists():
        console.print("[yellow]Warning: Not running on Arch Linux[/]")
        console.print("[yellow]Some tests may fail or be skipped[/]")

    # Check for required tools
    for tool in ["makepkg", "pacman", "git"]:
        result = subprocess.run(["which", tool], capture_output=True)
        if result.returncode != 0:
            console.print(f"[red]Required tool not found: {tool}[/]")
            return 1

    test = IntegrationTest(keep_temp=keep_temp)
    success = await test.run()

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
