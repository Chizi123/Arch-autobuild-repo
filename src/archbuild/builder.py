"""Package builder with parallel execution and proper locking."""

import asyncio
import fcntl
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from archbuild.aur import AURClient
from archbuild.config import Config, PackageOverride
from archbuild.logging import get_logger
from archbuild.resolver import DependencyResolver

logger = get_logger("builder")


class BuildStatus(Enum):
    """Build status for a package."""

    PENDING = "pending"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class BuildResult:
    """Result of a package build."""

    package: str
    status: BuildStatus
    version: str | None = None
    duration: float = 0.0
    error: str | None = None
    artifacts: list[Path] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class FileLock:
    """Context manager for file-based locking using flock."""

    def __init__(self, path: Path):
        """Initialize lock on file path.

        Args:
            path: Path to lock file
        """
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "FileLock":
        """Acquire exclusive lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        logger.debug(f"Acquired lock: {self.path}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Release lock."""
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            logger.debug(f"Released lock: {self.path}")


def _run_makepkg(
    package_dir: Path,
    sign: bool = False,
    key: str = "",
    clean: bool = True,
    skip_checksums: bool = False,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> tuple[bool, str, list[Path]]:
    """Run makepkg in a subprocess.

    This runs in a separate process for parallelization.

    Args:
        package_dir: Directory containing PKGBUILD
        sign: Whether to sign packages
        key: GPG key for signing
        clean: Clean build directory after build
        skip_checksums: Skip checksum verification
        extra_args: Additional makepkg arguments
        env_overrides: Environment variable overrides

    Returns:
        Tuple of (success, error_message, artifact_paths)
    """
    cmd = ["makepkg", "-s", "--noconfirm"]

    if clean:
        cmd.append("-c")
    if sign and key:
        cmd.extend(["--sign", "--key", key])
    if skip_checksums:
        cmd.append("--skipchecksums")
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    try:
        result = subprocess.run(
            cmd,
            cwd=package_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=3600,  # 1 hour timeout
        )

        if result.returncode != 0:
            return False, result.stderr or result.stdout, []

        # Find built packages
        artifacts = list(package_dir.glob("*.pkg.tar.*"))
        artifacts = [a for a in artifacts if not a.name.endswith(".sig")]

        return True, "", artifacts

    except subprocess.TimeoutExpired:
        return False, "Build timed out after 1 hour", []
    except Exception as e:
        return False, str(e), []


class Builder:
    """Package builder with parallel execution support."""

    def __init__(
        self,
        config: Config,
        aur_client: AURClient,
    ):
        """Initialize builder.

        Args:
            config: Application configuration
            aur_client: AUR client for package info
        """
        self.config = config
        self.aur_client = aur_client
        self.resolver = DependencyResolver(aur_client)
        self._lock_dir = config.repository.build_dir / ".locks"
        self._executor: ProcessPoolExecutor | None = None

    async def __aenter__(self) -> "Builder":
        """Async context manager entry."""
        if self.config.building.parallel:
            max_workers = self.config.building.max_workers
            self._executor = ProcessPoolExecutor(max_workers=max_workers)
            logger.info(f"Builder initialized with {max_workers} workers (parallel)")
        else:
            self._executor = None
            logger.info("Builder initialized (sequential)")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

    def _get_lock_path(self, package: str) -> Path:
        """Get lock file path for package.

        Args:
            package: Package name

        Returns:
            Path to lock file
        """
        return self._lock_dir / f"{package}.lock"

    def _get_package_dir(self, package: str) -> Path:
        """Get build directory for package.

        Args:
            package: Package name

        Returns:
            Path to package build directory
        """
        return self.config.repository.build_dir / package

    def _get_override(self, package: str) -> PackageOverride:
        """Get package-specific overrides.

        Args:
            package: Package name

        Returns:
            PackageOverride (default if not specified)
        """
        # Check for package-specific override first, then fall back to _default
        if package in self.config.package_overrides:
            return self.config.package_overrides[package]
        return self.config.package_overrides.get("_default", PackageOverride())

    async def _clone_or_update(self, package: str) -> bool:
        """Clone or update package from AUR.

        Args:
            package: Package name

        Returns:
            True if there were updates (or new clone)
        """
        pkg_dir = self._get_package_dir(package)

        if pkg_dir.exists():
            # Update existing repo
            result = subprocess.run(
                ["git", "reset", "--hard"],
                cwd=pkg_dir,
                capture_output=True,
            )
            result = subprocess.run(
                ["git", "pull"],
                cwd=pkg_dir,
                capture_output=True,
                text=True,
            )
            return "Already up to date" not in result.stdout
        else:
            # Clone new repo
            pkg_info = await self.aur_client.get_package(package)
            if not pkg_info:
                raise ValueError(f"Package not found in AUR: {package}")

            pkg_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", pkg_info.git_url, str(pkg_dir)],
                check=True,
                capture_output=True,
            )
            return True

    def _is_vcs_package(self, package_dir: Path) -> bool:
        """Check if package is a VCS package (needs rebuild on each run).

        Args:
            package_dir: Path to package directory

        Returns:
            True if VCS package
        """
        pkgbuild = package_dir / "PKGBUILD"
        if not pkgbuild.exists():
            return False

        content = pkgbuild.read_text()
        return "pkgver()" in content

    async def build_package(
        self,
        package: str,
        force: bool = False,
    ) -> BuildResult:
        """Build a single package.

        Args:
            package: Package name
            force: Force rebuild even if up to date

        Returns:
            BuildResult with status and artifacts
        """
        start_time = datetime.now()
        pkg_dir = self._get_package_dir(package)
        override = self._get_override(package)

        logger.info(f"Building package: {package}")

        try:
            # Clone or update
            has_updates = await self._clone_or_update(package)
            is_vcs = self._is_vcs_package(pkg_dir)

            # Skip if no updates and not forced
            if not has_updates and not is_vcs and not force:
                logger.info(f"Skipping {package}: already up to date")
                return BuildResult(
                    package=package,
                    status=BuildStatus.SKIPPED,
                    duration=(datetime.now() - start_time).total_seconds(),
                )

            # Run build with retries
            last_error = ""
            for attempt in range(self.config.building.retry_attempts):
                if attempt > 0:
                    delay = self.config.building.retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Retrying {package} (attempt {attempt + 1}/"
                        f"{self.config.building.retry_attempts}) after {delay}s"
                    )
                    await asyncio.sleep(delay)

                # Run makepkg in executor
                loop = asyncio.get_event_loop()
                success, error, artifacts = await loop.run_in_executor(
                    self._executor,
                    _run_makepkg,
                    pkg_dir,
                    self.config.signing.enabled,
                    self.config.signing.key,
                    self.config.building.clean,
                    override.skip_checksums,
                    override.extra_args,
                    override.env,
                )

                if success:
                    duration = (datetime.now() - start_time).total_seconds()
                    logger.info(f"Successfully built {package} in {duration:.1f}s")
                    return BuildResult(
                        package=package,
                        status=BuildStatus.SUCCESS,
                        duration=duration,
                        artifacts=artifacts,
                    )

                last_error = error

            # All retries failed
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"Failed to build {package}: {last_error}")
            return BuildResult(
                package=package,
                status=BuildStatus.FAILED,
                duration=duration,
                error=last_error,
            )

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.exception(f"Error building {package}")
            return BuildResult(
                package=package,
                status=BuildStatus.FAILED,
                duration=duration,
                error=str(e),
            )

    async def build_all(
        self,
        force: bool = False,
    ) -> list[BuildResult]:
        """Build all packages in build directory.

        Args:
            force: Force rebuild all packages

        Returns:
            List of build results
        """
        # Update system if configured
        if self.config.building.update_system:
            logger.info("Updating system...")
            subprocess.run(
                ["sudo", "pacman", "-Syu", "--noconfirm"],
                check=False,
            )

        # Find all packages
        build_dir = self.config.repository.build_dir
        packages = [
            d.name for d in build_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if not packages:
            logger.warning("No packages found in build directory")
            return []

        logger.info(f"Building {len(packages)} packages")

        # Build in parallel or sequentially
        if self.config.building.parallel:
            tasks = [self.build_package(pkg, force) for pkg in packages]
            results = await asyncio.gather(*tasks)
        else:
            results = []
            for pkg in packages:
                result = await self.build_package(pkg, force)
                results.append(result)

        # Summary
        success = sum(1 for r in results if r.status == BuildStatus.SUCCESS)
        failed = sum(1 for r in results if r.status == BuildStatus.FAILED)
        skipped = sum(1 for r in results if r.status == BuildStatus.SKIPPED)

        logger.info(f"Build complete: {success} succeeded, {failed} failed, {skipped} skipped")

        return list(results)

    async def add_package(self, package: str) -> BuildResult:
        """Add and build a new package with dependencies.

        Args:
            package: Package name

        Returns:
            BuildResult for the main package
        """
        logger.info(f"Adding package: {package}")

        # Resolve dependencies
        build_order = await self.resolver.resolve([package])

        # Build dependencies first
        results: list[BuildResult] = []
        for dep in build_order:
            if dep != package:
                logger.info(f"Building dependency: {dep}")
                result = await self.build_package(dep, force=True)
                results.append(result)

                if result.status == BuildStatus.FAILED:
                    logger.error(f"Dependency {dep} failed, aborting")
                    return BuildResult(
                        package=package,
                        status=BuildStatus.FAILED,
                        error=f"Dependency {dep} failed to build",
                    )

        # Build main package
        return await self.build_package(package, force=True)

    def remove_package(self, package: str) -> bool:
        """Remove a package from the build directory.

        Args:
            package: Package name

        Returns:
            True if removed successfully
        """
        pkg_dir = self._get_package_dir(package)

        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)
            logger.info(f"Removed package: {package}")
            return True

        logger.warning(f"Package not found: {package}")
        return False
