"""Tests for RepoManager, specifically build directory cleanup."""

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archrepobuild.builder import BuildResult, BuildStatus
from archrepobuild.config import (
    BuildingConfig,
    Config,
    PackageRetentionConfig,
    RepositoryConfig,
    SigningConfig,
)
from archrepobuild.repo import RepoManager


@pytest.fixture
def mock_config(tmp_path):
    """Create a real Config instance pointing at temp directories."""
    repo_path = tmp_path / "repo"
    build_path = tmp_path / "build"
    return Config(
        repository=RepositoryConfig(
            name="testrepo",
            path=repo_path,
            build_dir=build_path,
            compression="zst",
        ),
        building=BuildingConfig(
            parallel=False,
            max_workers=1,
            clean=True,
            update_system=False,
            retry_attempts=2,
        ),
        signing=SigningConfig(enabled=False),
        retention=PackageRetentionConfig(
            keep_versions=3,
            cleanup_on_build=True,
        ),
    )


@pytest.fixture
def repo_manager(mock_config):
    """Create a RepoManager with a mock config (no real subprocess calls)."""
    return RepoManager(mock_config)


def _create_package_dir(build_dir: Path, name: str, mtime_offset: float = 0) -> Path:
    """Helper to create a package directory with a given mtime."""
    pkg_dir = build_dir / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "PKGBUILD").write_text(f"# {name}")
    atime = time.time() + mtime_offset
    os.utime(pkg_dir, (atime, atime))
    return pkg_dir


def _create_package_file(repo_dir: Path, name: str, version: str,
                         mtime_offset: float = 0) -> Path:
    """Helper to create a fake .pkg.tar.zst file with proper naming."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name}-{version}-x86_64.pkg.tar.zst"
    pkg_file = repo_dir / filename
    pkg_file.write_text("fake package")
    atime = time.time() + mtime_offset
    os.utime(pkg_file, (atime, atime))
    return pkg_file


def _create_build_artifact(build_dir: Path, name: str, version: str,
                           mtime_offset: float = 0,
                           create_sig: bool = True) -> Path:
    """Helper to create a fake built package file inside a package build dir."""
    pkg_dir = build_dir / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name}-{version}-x86_64.pkg.tar.zst"
    pkg_file = pkg_dir / filename
    pkg_file.write_text("fake package")
    atime = time.time() + mtime_offset
    os.utime(pkg_file, (atime, atime))
    if create_sig:
        sig = pkg_file.with_suffix(pkg_file.suffix + ".sig")
        sig.write_text("fake sig")
    return pkg_file


class TestCleanupBuildDirArtifacts:
    """Tests for RepoManager._cleanup_build_dir_artifacts."""

    def test_missing_pkg_dir(self, repo_manager):
        """A package with no build directory is handled gracefully."""
        result = repo_manager._cleanup_build_dir_artifacts("nonexistent")
        assert result == 0

    def test_keeps_all_under_limit(self, repo_manager):
        """Fewer artifacts than keep_versions means nothing is removed."""
        repo_manager.config.retention.keep_versions = 3
        build_dir = repo_manager.config.repository.build_dir
        _create_build_artifact(build_dir, "emacs-git", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "emacs-git", "2.0-1", mtime_offset=0)

        result = repo_manager._cleanup_build_dir_artifacts("emacs-git")

        assert result == 0
        assert (build_dir / "emacs-git" / "emacs-git-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "emacs-git" / "emacs-git-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_removes_oldest_when_over_limit(self, repo_manager):
        """Oldest artifacts are removed once the count exceeds keep_versions."""
        repo_manager.config.retention.keep_versions = 2
        build_dir = repo_manager.config.repository.build_dir
        _create_build_artifact(build_dir, "emacs-git", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "emacs-git", "2.0-1", mtime_offset=-50)
        _create_build_artifact(build_dir, "emacs-git", "3.0-1", mtime_offset=0)

        result = repo_manager._cleanup_build_dir_artifacts("emacs-git")

        assert result == 1
        assert not (build_dir / "emacs-git" / "emacs-git-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "emacs-git" / "emacs-git-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "emacs-git" / "emacs-git-3.0-1-x86_64.pkg.tar.zst").exists()

    def test_removes_signatures_with_artifacts(self, repo_manager):
        """Signature files are removed along with their packages."""
        repo_manager.config.retention.keep_versions = 1
        build_dir = repo_manager.config.repository.build_dir
        _create_build_artifact(build_dir, "emacs-git", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "emacs-git", "2.0-1", mtime_offset=0)

        result = repo_manager._cleanup_build_dir_artifacts("emacs-git")

        assert result == 1
        assert not (build_dir / "emacs-git" / "emacs-git-1.0-1-x86_64.pkg.tar.zst").exists()
        assert not (build_dir / "emacs-git" / "emacs-git-1.0-1-x86_64.pkg.tar.zst.sig").exists()
        assert (build_dir / "emacs-git" / "emacs-git-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_only_matches_exact_package(self, repo_manager):
        """Artifacts of sub-packages or other packages are not touched."""
        repo_manager.config.retention.keep_versions = 1
        build_dir = repo_manager.config.repository.build_dir
        _create_build_artifact(build_dir, "emacs", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "emacs", "2.0-1", mtime_offset=0)
        _create_build_artifact(build_dir, "emacs-git", "9.0-1", mtime_offset=0)

        result = repo_manager._cleanup_build_dir_artifacts("emacs")

        assert result == 1
        assert (build_dir / "emacs-git" / "emacs-git-9.0-1-x86_64.pkg.tar.zst").exists()


class TestCleanupBuildDirSources:
    """Tests for RepoManager._cleanup_build_dir_sources."""

    def _make_pkg_dir(self, repo_manager, name) -> Path:
        pkg_dir = repo_manager.config.repository.build_dir / name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        return pkg_dir

    @patch("archrepobuild.repo.subprocess.run")
    def test_removes_only_old_sources(self, mock_run, repo_manager):
        """Sources referenced by the current PKGBUILD are kept, old ones removed."""
        pkg_dir = self._make_pkg_dir(repo_manager, "beeper-v4-bin")
        (pkg_dir / ".SRCINFO").write_text(
            "source = App-2.0.AppImage\n"
            "source = src-2.0-1.tar.gz\n"
        )
        (pkg_dir / "App-2.0.AppImage").write_text("current source")
        (pkg_dir / "src-2.0-1.tar.gz").write_text("current source")
        (pkg_dir / "App-1.0.AppImage").write_text("stale source")
        (pkg_dir / "src-1.0-1.tar.gz").write_text("stale source")
        (pkg_dir / "PKGBUILD").write_text("# pkgbuild")
        (pkg_dir / "beeper-v4-bin-2.0-1-x86_64.pkg.tar.zst").write_text("pkg")
        (pkg_dir / "beeper-v4-bin-2.0-1-x86_64.pkg.tar.zst.sig").write_text("sig")

        mock_run.return_value.stdout = "PKGBUILD\n.SRCINFO\n"

        result = repo_manager._cleanup_build_dir_sources("beeper-v4-bin")

        assert result == 2
        assert (pkg_dir / "App-2.0.AppImage").exists()
        assert (pkg_dir / "src-2.0-1.tar.gz").exists()
        assert not (pkg_dir / "App-1.0.AppImage").exists()
        assert not (pkg_dir / "src-1.0-1.tar.gz").exists()
        assert (pkg_dir / "PKGBUILD").exists()
        assert (pkg_dir / "beeper-v4-bin-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (pkg_dir / "beeper-v4-bin-2.0-1-x86_64.pkg.tar.zst.sig").exists()

    def test_parses_url_and_rename_sources(self, repo_manager):
        """URL basenames and name::url rename syntax are resolved from SRCINFO."""
        pkg_dir = self._make_pkg_dir(repo_manager, "test-pkg")
        (pkg_dir / ".SRCINFO").write_text(
            "source = App-3.0.AppImage::https://example.com/download/v3.AppImage\n"
            "source = https://example.com/cfg-1.0.tar.gz\n"
            "source = local-file.deb\n"
        )
        (pkg_dir / "App-3.0.AppImage").write_text("renamed")
        (pkg_dir / "cfg-1.0.tar.gz").write_text("from url")
        (pkg_dir / "local-file.deb").write_text("local")
        (pkg_dir / "stale.deb").write_text("old")

        with patch("archrepobuild.repo.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            result = repo_manager._cleanup_build_dir_sources("test-pkg")

        assert result == 1
        assert (pkg_dir / "App-3.0.AppImage").exists()
        assert (pkg_dir / "cfg-1.0.tar.gz").exists()
        assert (pkg_dir / "local-file.deb").exists()
        assert not (pkg_dir / "stale.deb").exists()

    @patch("archrepobuild.repo.subprocess.run")
    def test_keeps_git_tracked_archives(self, mock_run, repo_manager):
        """Even an archive-named file that is git-tracked is kept."""
        pkg_dir = self._make_pkg_dir(repo_manager, "test-pkg")
        (pkg_dir / "vendored.tar.gz").write_text("tracked archive")

        mock_run.return_value.stdout = "vendored.tar.gz\n"

        result = repo_manager._cleanup_build_dir_sources("test-pkg")

        assert result == 0
        assert (pkg_dir / "vendored.tar.gz").exists()

    @patch("archrepobuild.repo.subprocess.run", side_effect=subprocess.CalledProcessError(128, "git"))
    def test_handles_non_git_dir(self, mock_run, repo_manager):
        """Without a git repo, stale archives are still removed; current kept."""
        pkg_dir = self._make_pkg_dir(repo_manager, "test-pkg")
        (pkg_dir / ".git").mkdir()
        (pkg_dir / ".SRCINFO").write_text("source = App-1.0.AppImage\n")
        (pkg_dir / "App-1.0.AppImage").write_text("current")
        (pkg_dir / "App-0.9.AppImage").write_text("stale")

        result = repo_manager._cleanup_build_dir_sources("test-pkg")

        assert result == 1
        assert (pkg_dir / "App-1.0.AppImage").exists()
        assert not (pkg_dir / "App-0.9.AppImage").exists()
        assert (pkg_dir / ".git").is_dir()

    def test_missing_srcinfo_removes_all_untracked_archives(self, repo_manager):
        """Without SRCINFO, all untracked archives are treated as stale."""
        pkg_dir = self._make_pkg_dir(repo_manager, "test-pkg")
        (pkg_dir / "PKGBUILD").write_text("# pkgbuild")
        (pkg_dir / "App-1.0.AppImage").write_text("source")

        with patch("archrepobuild.repo.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "PKGBUILD\n"
            result = repo_manager._cleanup_build_dir_sources("test-pkg")

        assert result == 1
        assert not (pkg_dir / "App-1.0.AppImage").exists()
        assert (pkg_dir / "PKGBUILD").exists()

    def test_missing_pkg_dir(self, repo_manager):
        """A package with no build directory is handled gracefully."""
        result = repo_manager._cleanup_build_dir_sources("nonexistent")
        assert result == 0


class TestCleanup:
    """Tests for RepoManager.cleanup integration."""

    @patch.object(RepoManager, "_remove_old_packages", return_value=2)
    @patch.object(RepoManager, "_cleanup_build_dir_artifacts", return_value=3)
    def test_calls_all_cleanup_methods(
        self, mock_build_artifacts, mock_repo_cleanup, repo_manager
    ):
        """cleanup() should call all cleanup methods for each package."""
        mock_pkg = MagicMock()
        mock_pkg.name = "test-pkg"
        with patch.object(repo_manager, "list_packages", return_value=[mock_pkg]):
            total = repo_manager.cleanup()

        assert total == 5
        mock_repo_cleanup.assert_called_once_with("test-pkg")
        mock_build_artifacts.assert_called_once_with("test-pkg")

    @patch.object(RepoManager, "_remove_old_packages", return_value=0)
    @patch.object(RepoManager, "_cleanup_build_dir_artifacts", return_value=0)
    def test_returns_zero_when_nothing_to_clean(
        self, mock_build_artifacts, mock_repo_cleanup, repo_manager
    ):
        """cleanup() returns 0 when no cleanup is needed."""
        with patch.object(repo_manager, "list_packages", return_value=[]):
            total = repo_manager.cleanup()

        assert total == 0

    def test_cleanup_includes_packages_only_in_build_dir(self, repo_manager):
        """cleanup() also handles packages that exist only in the build dir."""
        build_dir = repo_manager.config.repository.build_dir
        _create_build_artifact(build_dir, "only-build-pkg", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "only-build-pkg", "2.0-1", mtime_offset=0)
        repo_manager.config.retention.keep_versions = 1

        with patch.object(repo_manager, "list_packages", return_value=[]):
            with patch.object(repo_manager, "_remove_old_packages", return_value=0):
                total = repo_manager.cleanup()

        assert total == 1
        assert not (build_dir / "only-build-pkg" / "only-build-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "only-build-pkg" / "only-build-pkg-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_cleanup_calls_sources_when_enabled(self, repo_manager):
        """cleanup() calls source cleanup for each package when clean_sources is on."""
        repo_manager.config.retention.clean_sources = True
        mock_pkg = MagicMock()
        mock_pkg.name = "test-pkg"
        with patch.object(repo_manager, "list_packages", return_value=[mock_pkg]):
            with patch.object(repo_manager, "_remove_old_packages", return_value=0):
                with patch.object(repo_manager, "_cleanup_build_dir_artifacts", return_value=0):
                    with patch.object(repo_manager, "_cleanup_build_dir_sources") as mock_sources:
                        repo_manager.cleanup()

        mock_sources.assert_called_once_with("test-pkg")


class TestCleanupFullRealIntegration:
    """End-to-end test with real files in both repo and build dirs."""

    def test_cleanup_removes_old_from_repo_keeps_build_dirs(self, mock_config):
        """cleanup() removes old repo versions but never removes build dirs."""
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path
        build_dir = mock_config.repository.build_dir

        # Create repo package files (2 versions of pkg-a, 1 version of pkg-b)
        _create_package_file(repo_dir, "pkg-a", "1.0-1", mtime_offset=-100)
        _create_package_file(repo_dir, "pkg-a", "2.0-1", mtime_offset=0)
        _create_package_file(repo_dir, "pkg-b", "1.0-1", mtime_offset=0)

        # Create build dirs
        _create_package_dir(build_dir, "pkg-a", mtime_offset=-100)
        _create_package_dir(build_dir, "pkg-b", mtime_offset=0)

        total = manager.cleanup()

        assert total == 1
        # repo: pkg-a 1.0 should be removed, pkg-a 2.0 kept, pkg-b 1.0 kept
        assert not (repo_dir / "pkg-a-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "pkg-a-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "pkg-b-1.0-1-x86_64.pkg.tar.zst").exists()
        # build dirs are never touched by cleanup
        assert (build_dir / "pkg-a").exists()
        assert (build_dir / "pkg-b").exists()

    def test_cleanup_removes_old_artifacts_from_build_dir(self, mock_config):
        """cleanup() real execution: removes old artifacts within a package build dir."""
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path
        build_dir = mock_config.repository.build_dir

        _create_package_file(repo_dir, "emacs-git", "1.0-1", mtime_offset=0)
        _create_build_artifact(build_dir, "emacs-git", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "emacs-git", "2.0-1", mtime_offset=0)

        total = manager.cleanup()

        assert total == 1
        # Only the most recent artifact remains in the build dir
        assert not (build_dir / "emacs-git" / "emacs-git-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "emacs-git" / "emacs-git-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_cleanup_removes_nothing_when_under_limits(self, mock_config):
        """cleanup() real execution: nothing removed when under keep_versions."""
        mock_config.retention.keep_versions = 3
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path
        build_dir = mock_config.repository.build_dir

        _create_package_file(repo_dir, "pkg-a", "1.0-1", mtime_offset=0)
        _create_package_dir(build_dir, "pkg-a", mtime_offset=0)

        total = manager.cleanup()

        assert total == 0
        assert (repo_dir / "pkg-a-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "pkg-a").exists()


class TestAddPackagesWiring:
    """Tests for add_packages wiring of retention and cleanup."""

    def _make_artifact(self, build_dir, name, version):
        """Create a fake artifact for use as a BuildResult artifact."""
        pkg_dir = build_dir / name
        pkg_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{name}-{version}-x86_64.pkg.tar.zst"
        artifact = pkg_dir / filename
        artifact.write_text("fake")
        return artifact

    def _call_add_packages(self, manager, name, version, status=BuildStatus.SUCCESS):
        """Helper to call add_packages with a fake build result."""
        artifact = self._make_artifact(
            manager.config.repository.build_dir, name, version
        )
        result = BuildResult(
            package=name,
            status=status,
            artifacts=[artifact],
        )
        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            return manager.add_packages(result)

    def test_remove_old_packages_called_when_cleanup_on_build(self, mock_config):
        """_remove_old_packages called for built package when cleanup_on_build=True."""
        mock_config.retention.cleanup_on_build = True
        manager = RepoManager(mock_config)

        with patch.object(manager, "_remove_old_packages") as mock_rm:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_rm.assert_called_once_with("test-pkg")

    def test_cleanup_build_dir_artifacts_called_when_cleanup_on_build(self, mock_config):
        """_cleanup_build_dir_artifacts called for built package when cleanup_on_build=True."""
        mock_config.retention.cleanup_on_build = True
        manager = RepoManager(mock_config)

        with patch.object(manager, "_cleanup_build_dir_artifacts") as mock_clean:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_clean.assert_called_once_with("test-pkg")

    def test_cleanup_build_dir_artifacts_skipped_when_not_configured(self, mock_config):
        """_cleanup_build_dir_artifacts skipped when cleanup_on_build=False."""
        mock_config.retention.cleanup_on_build = False
        manager = RepoManager(mock_config)

        with patch.object(manager, "_cleanup_build_dir_artifacts") as mock_clean:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_clean.assert_not_called()

    def test_remove_old_packages_skipped_when_not_configured(self, mock_config):
        """_remove_old_packages skipped when cleanup_on_build=False."""
        mock_config.retention.cleanup_on_build = False
        manager = RepoManager(mock_config)

        with patch.object(manager, "_remove_old_packages") as mock_rm:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_rm.assert_not_called()

    def test_cleanup_sources_called_when_clean_sources_on(self, mock_config):
        """_cleanup_build_dir_sources called when clean_sources is enabled."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.clean_sources = True
        manager = RepoManager(mock_config)

        with patch.object(manager, "_cleanup_build_dir_sources") as mock_clean:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_clean.assert_called_once_with("test-pkg")

    def test_cleanup_sources_skipped_when_not_configured(self, mock_config):
        """_cleanup_build_dir_sources skipped when clean_sources is off."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.clean_sources = False
        manager = RepoManager(mock_config)

        with patch.object(manager, "_cleanup_build_dir_sources") as mock_clean:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_clean.assert_not_called()

    def test_never_deletes_build_dir(self, mock_config):
        """add_packages never removes build directories (only the explicit remove command does)."""
        mock_config.retention.cleanup_on_build = True
        manager = RepoManager(mock_config)

        self._call_add_packages(manager, "test-pkg", "1.0-1")

        assert (manager.config.repository.build_dir / "test-pkg").exists()

    def test_skipped_when_build_not_successful(self, mock_config):
        """Neither cleanup method called when build failed."""
        manager = RepoManager(mock_config)
        result = BuildResult(package="test-pkg", status=BuildStatus.FAILED)

        with patch.object(manager, "_remove_old_packages") as mock_rm:
            manager.add_packages(result)

        mock_rm.assert_not_called()

    def test_skipped_when_no_artifacts(self, mock_config):
        """Neither cleanup method called when there are no artifacts."""
        manager = RepoManager(mock_config)
        result = BuildResult(package="test-pkg", status=BuildStatus.SUCCESS, artifacts=[])

        with patch.object(manager, "_remove_old_packages") as mock_rm:
            manager.add_packages(result)

        mock_rm.assert_not_called()


class TestAddPackagesRealCleanup:
    """Real _remove_old_packages execution through add_packages."""

    def test_add_packages_removes_old_versions(self, mock_config):
        """add_packages runs real _remove_old_packages on the built package."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path

        # Pre-create old versions directly in repo dir (as if they existed before)
        _create_package_file(repo_dir, "test-pkg", "1.0-1", mtime_offset=-100)
        _create_package_file(repo_dir, "test-pkg", "2.0-1", mtime_offset=-50)

        # Build result with a newer artifact
        build_dir = mock_config.repository.build_dir
        artifact = build_dir / "test-pkg" / "test-pkg-3.0-1-x86_64.pkg.tar.zst"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("new package")

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            added = manager.add_packages(result)

        # Only the most recent version should remain (3.0 kept, 2.0 removed)
        assert not (repo_dir / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert not (repo_dir / "test-pkg-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "test-pkg-3.0-1-x86_64.pkg.tar.zst").exists()
        assert added == ["test-pkg-3.0-1-x86_64.pkg.tar.zst"]

    def test_add_packages_keeps_all_when_under_limit(self, mock_config):
        """add_packages keeps all old versions when under keep_versions limit."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.keep_versions = 3
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path

        # One pre-existing version
        _create_package_file(repo_dir, "test-pkg", "1.0-1", mtime_offset=-50)

        # Newer artifact
        build_dir = mock_config.repository.build_dir
        artifact = build_dir / "test-pkg" / "test-pkg-2.0-1-x86_64.pkg.tar.zst"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("new package")

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            manager.add_packages(result)

        # Both versions should be kept (2 total <= keep_versions=3)
        assert (repo_dir / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "test-pkg-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_add_packages_does_not_clean_when_cleanup_off(self, mock_config):
        """add_packages does not remove old versions when cleanup_on_build=False."""
        mock_config.retention.cleanup_on_build = False
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path

        # Pre-existing old version
        _create_package_file(repo_dir, "test-pkg", "1.0-1", mtime_offset=-100)

        # Newer artifact
        build_dir = mock_config.repository.build_dir
        artifact = build_dir / "test-pkg" / "test-pkg-2.0-1-x86_64.pkg.tar.zst"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("new package")

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            manager.add_packages(result)

        # Old version should still exist since cleanup_on_build=False
        assert (repo_dir / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "test-pkg-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_add_packages_removes_old_build_artifacts(self, mock_config):
        """add_packages cleans old artifacts in the build dir beyond keep_versions."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        build_dir = mock_config.repository.build_dir

        # Pre-create old artifacts for this package in its build dir
        _create_build_artifact(build_dir, "test-pkg", "1.0-1", mtime_offset=-100)
        _create_build_artifact(build_dir, "test-pkg", "2.0-1", mtime_offset=-50)

        # Build result with a newer artifact
        artifact = _create_build_artifact(build_dir, "test-pkg", "3.0-1", mtime_offset=0)

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            manager.add_packages(result)

        # Only the most recent artifact remains in the build dir (1.0, 2.0 removed)
        assert not (build_dir / "test-pkg" / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert not (build_dir / "test-pkg" / "test-pkg-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "test-pkg" / "test-pkg-3.0-1-x86_64.pkg.tar.zst").exists()

    def test_add_packages_keeps_build_artifacts_when_cleanup_off(self, mock_config):
        """add_packages does not remove build dir artifacts when cleanup_on_build=False."""
        mock_config.retention.cleanup_on_build = False
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        build_dir = mock_config.repository.build_dir

        _create_build_artifact(build_dir, "test-pkg", "1.0-1", mtime_offset=-100)
        artifact = _create_build_artifact(build_dir, "test-pkg", "2.0-1", mtime_offset=0)

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            manager.add_packages(result)

        assert (build_dir / "test-pkg" / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (build_dir / "test-pkg" / "test-pkg-2.0-1-x86_64.pkg.tar.zst").exists()

    def test_add_packages_cleans_only_built_package(self, mock_config):
        """add_packages only removes old versions of the built package, not others."""
        mock_config.retention.cleanup_on_build = True
        mock_config.retention.keep_versions = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path

        # Pre-existing versions of different packages
        _create_package_file(repo_dir, "other-pkg", "1.0-1", mtime_offset=-100)
        _create_package_file(repo_dir, "other-pkg", "2.0-1", mtime_offset=0)

        # Build artifact for a different package
        build_dir = mock_config.repository.build_dir
        artifact = build_dir / "test-pkg" / "test-pkg-1.0-1-x86_64.pkg.tar.zst"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("new package")

        result = BuildResult(
            package="test-pkg",
            status=BuildStatus.SUCCESS,
            artifacts=[artifact],
        )

        with patch.object(manager, "_run_repo_command", return_value=MagicMock(returncode=0)):
            manager.add_packages(result)

        # other-pkg versions should not be touched
        assert (repo_dir / "other-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "other-pkg-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "test-pkg-1.0-1-x86_64.pkg.tar.zst").exists()
