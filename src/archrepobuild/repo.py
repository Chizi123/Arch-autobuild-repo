"""Repository management with repo-add/repo-remove wrappers."""

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from archrepobuild.builder import BuildResult, BuildStatus, FileLock
from archrepobuild.config import Config
from archrepobuild.logging import get_logger

logger = get_logger("repo")

# Suffixes of downloaded source archives makepkg leaves in the build dir.
# These are untracked in the AUR clone and re-downloaded on the next build,
# so they can be removed to reclaim space.
SOURCE_ARCHIVE_SUFFIXES = (
    ".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst",
    ".tgz", ".tbz2", ".txz", ".zip", ".gz", ".xz", ".bz2", ".zst",
    ".deb", ".rpm", ".appimage", ".dmg",
)


@dataclass
class PackageInfo:
    """Information about a package in the repository."""

    name: str
    version: str
    filename: str
    size: int
    modified: datetime


class RepoManager:
    """Manage the pacman repository database."""

    def __init__(self, config: Config):
        """Initialize repository manager.

        Args:
            config: Application configuration
        """
        self.config = config
        self._lock_path = config.repository.path / ".repo.lock"

    @property
    def db_path(self) -> Path:
        """Get path to repository database file."""
        compression = self.config.repository.compression or "zst"
        return self.config.repository.path / f"{self.config.repository.name}.db.tar.{compression}"

    def _get_repo_lock(self) -> FileLock:
        """Get lock for repository operations."""
        return FileLock(self._lock_path)

    def _run_repo_command(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a repo-add or repo-remove command.

        Args:
            cmd: Command and arguments

        Returns:
            Completed process result
        """
        if self.config.signing.enabled and self.config.signing.key:
            cmd.extend(["--sign", "--key", self.config.signing.key])

        logger.debug(f"Running: {' '.join(cmd)}")
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.config.repository.path,
        )

    def _vercmp(self, v1: str, v2: str) -> int:
        """Compare two version strings using vercmp.

        Returns:
            >0 if v1 > v2, <0 if v1 < v2, 0 if equal
        """
        try:
            result = subprocess.run(
                ["vercmp", v1, v2],
                capture_output=True,
                text=True,
                check=True,
            )
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 0

    def _parse_pkg_filename(self, filename: str) -> tuple[str, str, str]:
        """Parse package name, version, and architecture from a filename.

        Args:
            filename: Package filename (e.g. name-version-rel-arch.pkg.tar.zst)

        Returns:
            Tuple of (package_name, version-release, architecture)
        """
        # Remove suffixes
        stem = filename
        for suffix in [".pkg.tar.zst", ".pkg.tar.xz", ".pkg.tar.gz", ".pkg.tar.bz2", ".pkg.tar"]:
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break

        # Format: name-version-rel-arch
        parts = stem.rsplit("-", 3)
        if len(parts) == 4:
            name = parts[0]
            version = f"{parts[1]}-{parts[2]}"
            arch = parts[3]
            return name, version, arch

        return stem, "unknown", "unknown"

    def ensure_repo_exists(self) -> None:
        """Ensure repository directory and database exist."""
        self.config.repository.path.mkdir(parents=True, exist_ok=True)

        if not self.db_path.exists():
            logger.info(f"Creating new repository: {self.config.repository.name}")
            # Create empty database
            result = self._run_repo_command([
                "repo-add",
                str(self.db_path),
            ])
            if result.returncode != 0:
                logger.warning(f"Could not create empty database: {result.stderr}")

    def add_packages(self, build_result: BuildResult) -> list[str]:
        """Add built packages to the repository.

        Args:
            build_result: Result from package build

        Returns:
            List of filenames added successfully
        """
        if build_result.status != BuildStatus.SUCCESS:
            logger.warning(f"Cannot add {build_result.package}: build was not successful")
            return []

        if not build_result.artifacts:
            logger.warning(f"No artifacts to add for {build_result.package}")
            return []

        with self._get_repo_lock():
            self.ensure_repo_exists()

            # Group artifacts by (name, arch) and only keep the latest version
            latest_artifacts: dict[tuple[str, str], Path] = {}
            for artifact in build_result.artifacts:
                name, version, arch = self._parse_pkg_filename(artifact.name)
                key = (name, arch)
                if key not in latest_artifacts:
                    latest_artifacts[key] = artifact
                else:
                    _, current_best_ver, _ = self._parse_pkg_filename(latest_artifacts[key].name)
                    if self._vercmp(version, current_best_ver) > 0:
                        latest_artifacts[key] = artifact

            artifacts_to_copy = list(latest_artifacts.values())

            # Copy artifacts to repo directory
            copied_files: list[Path] = []
            for artifact in artifacts_to_copy:
                dest = self.config.repository.path / artifact.name
                shutil.copy2(artifact, dest)
                copied_files.append(dest)

                # Also copy signature if exists
                sig_path = artifact.with_suffix(artifact.suffix + ".sig")
                if sig_path.exists():
                    shutil.copy2(sig_path, self.config.repository.path / sig_path.name)

            # Add to database
            result = self._run_repo_command([
                "repo-add",
                str(self.db_path),
            ] + [str(f) for f in copied_files])

            if result.returncode != 0:
                logger.error(f"Failed to add packages to database: {result.stderr}")
                return []

            # Clean up old versions in repo and build dir for each package name added
            if self.config.retention.cleanup_on_build:
                for (name, arch) in latest_artifacts.keys():
                    self._remove_old_packages(name)
                    self._cleanup_build_dir_artifacts(name)
                    if self.config.retention.clean_sources:
                        self._cleanup_build_dir_sources(name)

            added_names = [f.name for f in copied_files]
            logger.info(f"Added to repository: {', '.join(added_names)}")
            return added_names

    def remove_package(self, package: str) -> bool:
        """Remove a package from the repository.

        Args:
            package: Package name to remove

        Returns:
            True if removed successfully
        """
        with self._get_repo_lock():
            # Remove from database
            result = self._run_repo_command([
                "repo-remove",
                str(self.db_path),
                package,
            ])

            if result.returncode != 0:
                logger.warning(f"Failed to remove {package} from database: {result.stderr}")

            # Remove package files
            removed = 0
            for f in self.config.repository.path.glob(f"{package}-*.pkg.tar.*"):
                if f.name.endswith(".sig"):
                    continue
                name, _, _ = self._parse_pkg_filename(f.name)
                if name == package:
                    f.unlink()
                    removed += 1
                    # Also remove signature
                    sig = f.with_suffix(f.suffix + ".sig")
                    if sig.exists():
                        sig.unlink()

            logger.info(f"Removed {package} ({removed} files)")
            return True

    def _remove_old_packages(self, package: str) -> int:
        """Remove old versions of a package beyond retention limit.

        Args:
            package: Package name

        Returns:
            Number of packages removed
        """
        keep_versions = self.config.retention.keep_versions
        pattern = f"{package}-*.pkg.tar.*"

        # Find all package files
        files = list(self.config.repository.path.glob(pattern))
        files = [f for f in files if not f.name.endswith(".sig")]
        # Filter to ensure exact package name match (to avoid matching sub-packages)
        files = [f for f in files if self._parse_pkg_filename(f.name)[0] == package]

        if len(files) <= keep_versions:
            return 0

        # Sort by modification time, oldest first
        files.sort(key=lambda f: f.stat().st_mtime)

        # Remove oldest files exceeding retention
        to_remove = files[:-keep_versions] if keep_versions > 0 else files
        removed = 0

        for f in to_remove:
            f.unlink()
            # Also remove signature
            sig = f.with_suffix(f.suffix + ".sig")
            if sig.exists():
                sig.unlink()
            removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old version(s) of {package}")

        return removed

    def list_packages(self) -> list[PackageInfo]:
        """List all packages in the repository.

        Returns:
            List of PackageInfo objects
        """
        packages: list[PackageInfo] = []

        for f in self.config.repository.path.glob("*.pkg.tar.*"):
            if f.name.endswith(".sig"):
                continue

            name, version, arch = self._parse_pkg_filename(f.name)

            stat = f.stat()
            packages.append(PackageInfo(
                name=name,
                version=version,
                filename=f.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            ))

        return sorted(packages, key=lambda p: p.name)

    def rebuild_database(self) -> bool:
        """Rebuild the repository database from scratch.

        Returns:
            True if successful
        """
        with self._get_repo_lock():
            logger.info("Rebuilding repository database")

            # Remove old database
            for f in self.config.repository.path.glob(f"{self.config.repository.name}.db*"):
                f.unlink()
            for f in self.config.repository.path.glob(f"{self.config.repository.name}.files*"):
                f.unlink()

            # Find all packages
            packages = list(self.config.repository.path.glob("*.pkg.tar.*"))
            packages = [p for p in packages if not p.name.endswith(".sig")]

            if not packages:
                logger.warning("No packages found to add to database")
                return True

            # Add all packages
            result = self._run_repo_command([
                "repo-add",
                str(self.db_path),
            ] + [str(p) for p in packages])

            if result.returncode != 0:
                logger.error(f"Failed to rebuild database: {result.stderr}")
                return False

            logger.info(f"Database rebuilt with {len(packages)} packages")
            return True

    def _cleanup_build_dir_artifacts(self, package: str) -> int:
        """Remove old package files from a package's build directory beyond retention limit.

        Honours the same keep_versions retention as the repository, so build
        directories do not accumulate unbounded numbers of built packages.

        Args:
            package: Package name

        Returns:
            Number of package files removed
        """
        keep_versions = self.config.retention.keep_versions
        pkg_dir = self.config.repository.build_dir / package

        if not pkg_dir.is_dir():
            return 0

        files = list(pkg_dir.glob(f"{package}-*.pkg.tar.*"))
        files = [f for f in files if not f.name.endswith(".sig")]
        files = [f for f in files if self._parse_pkg_filename(f.name)[0] == package]

        if len(files) <= keep_versions:
            return 0

        # Sort by modification time, oldest first
        files.sort(key=lambda f: f.stat().st_mtime)

        # Remove oldest files exceeding retention
        to_remove = files[:-keep_versions] if keep_versions > 0 else files
        removed = 0

        for f in to_remove:
            f.unlink()
            # Also remove signature
            sig = f.with_suffix(f.suffix + ".sig")
            if sig.exists():
                sig.unlink()
            removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old build artifact(s) of {package}")

        return removed

    def _current_source_filenames(self, pkg_dir: Path) -> set[str]:
        """Get the source filenames referenced by the package's current PKGBUILD.

        Parses the .SRCINFO file (generated by makepkg) which contains the
        resolved source filenames, handling URL basenames and `name::url`
        rename syntax.

        Args:
            pkg_dir: Path to the package build directory

        Returns:
            Set of source filenames currently referenced
        """
        srcinfo = pkg_dir / ".SRCINFO"
        names: set[str] = set()
        if not srcinfo.is_file():
            return names

        for line in srcinfo.read_text().splitlines():
            stripped = line.strip()
            if not stripped.startswith("source") or "=" not in stripped:
                continue
            value = stripped.split("=", 1)[1].strip()
            if not value or value.startswith("$"):
                continue
            if "::" in value:
                # Rename syntax: localname::url -> keep localname
                name = value.split("::", 1)[0]
            elif value.startswith(("http://", "https://", "ftp://", "file://", "git://", "git+")):
                # Full URL -> downloaded under the basename
                name = value.split("?", 1)[0].rsplit("/", 1)[-1]
            else:
                name = value
            if name:
                names.add(name)

        return names

    def _cleanup_build_dir_sources(self, package: str) -> int:
        """Remove old downloaded source archives from a package's build directory.

        Sources referenced by the current PKGBUILD (via .SRCINFO) are kept;
        only stale untracked archive files (old AppImages, tar.gz, debs, etc.)
        are removed. Files tracked by the AUR clone (PKGBUILD, patches, install
        scripts) are never touched. Sources are re-downloaded by makepkg if
        needed, so removal only costs bandwidth, not rebuilds.

        Args:
            package: Package name

        Returns:
            Number of source files removed
        """
        pkg_dir = self.config.repository.build_dir / package

        if not pkg_dir.is_dir():
            return 0

        # Tracked files (PKGBUILD, patches, install files) are never removed
        tracked: set[str] | None = None
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=pkg_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            tracked = set(result.stdout.split())
        except (subprocess.CalledProcessError, FileNotFoundError):
            tracked = None

        current_sources = self._current_source_filenames(pkg_dir)

        removed = 0
        for f in pkg_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if name.startswith(".") or name.endswith(".sig"):
                continue
            if ".pkg.tar." in name:
                continue
            if tracked is not None and name in tracked:
                continue
            if not name.lower().endswith(SOURCE_ARCHIVE_SUFFIXES):
                continue
            if name in current_sources:
                continue

            f.unlink()
            removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} old source archive(s) from {package}")

        return removed

    def cleanup(self) -> int:
        """Run cleanup on all packages, removing old versions.

        Returns:
            Total number of old packages removed
        """
        # Get unique package names
        packages = self.list_packages()
        unique_names = set(p.name for p in packages)

        # Also include packages that have artifacts in the build dir
        build_dir = self.config.repository.build_dir
        if build_dir.exists():
            unique_names.update(
                d.name for d in build_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name != "downloads"
            )

        total_removed = 0
        for name in unique_names:
            total_removed += self._remove_old_packages(name)
            total_removed += self._cleanup_build_dir_artifacts(name)
            if self.config.retention.clean_sources:
                total_removed += self._cleanup_build_dir_sources(name)

        return total_removed

    def check_integrity(self) -> list[str]:
        """Check repository integrity.

        Returns:
            List of issues found
        """
        issues: list[str] = []

        # Check database exists
        if not self.db_path.exists():
            issues.append(f"Database not found: {self.db_path}")
            return issues

        # Check for orphaned packages (in dir but not in db)
        # This would require parsing the db, simplified for now

        # Check for missing signatures
        if self.config.signing.enabled:
            for pkg in self.config.repository.path.glob("*.pkg.tar.*"):
                if pkg.name.endswith(".sig"):
                    continue
                sig = pkg.with_suffix(pkg.suffix + ".sig")
                if not sig.exists():
                    issues.append(f"Missing signature: {pkg.name}")

        return issues
