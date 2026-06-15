"""Tests for RepoManager, specifically build directory cleanup."""

import os
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
            max_build_packages=0,
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


class TestCleanupBuildDir:
    """Tests for RepoManager._cleanup_build_dir."""

    def test_disabled_when_zero(self, repo_manager):
        """max_build_packages=0 means no cleanup."""
        repo_manager.config.retention.max_build_packages = 0
        _create_package_dir(repo_manager.config.repository.build_dir, "pkg-a")
        _create_package_dir(repo_manager.config.repository.build_dir, "pkg-b")

        result = repo_manager._cleanup_build_dir()

        assert result == 0
        assert (repo_manager.config.repository.build_dir / "pkg-a").exists()
        assert (repo_manager.config.repository.build_dir / "pkg-b").exists()

    def test_no_build_dir(self, repo_manager):
        """Non-existent build dir is handled gracefully."""
        repo_manager.config.retention.max_build_packages = 5
        result = repo_manager._cleanup_build_dir()
        assert result == 0

    def test_under_limit_keeps_all(self, repo_manager):
        """Fewer dirs than limit means nothing is removed."""
        repo_manager.config.retention.max_build_packages = 5
        build_dir = repo_manager.config.repository.build_dir
        _create_package_dir(build_dir, "pkg-a")
        _create_package_dir(build_dir, "pkg-b")
        _create_package_dir(build_dir, "pkg-c")

        result = repo_manager._cleanup_build_dir()

        assert result == 0
        assert len(list(build_dir.iterdir())) == 3

    def test_removes_oldest_when_over_limit(self, repo_manager):
        """Oldest directories are removed when count exceeds max_build_packages."""
        repo_manager.config.retention.max_build_packages = 2
        build_dir = repo_manager.config.repository.build_dir
        _create_package_dir(build_dir, "oldest", mtime_offset=-100)
        _create_package_dir(build_dir, "middle", mtime_offset=-50)
        _create_package_dir(build_dir, "newest", mtime_offset=0)

        result = repo_manager._cleanup_build_dir()

        assert result == 1
        assert not (build_dir / "oldest").exists()
        assert (build_dir / "middle").exists()
        assert (build_dir / "newest").exists()

    def test_removes_multiple_when_way_over_limit(self, repo_manager):
        """All excess directories are removed, keeping only the most recent N."""
        repo_manager.config.retention.max_build_packages = 2
        build_dir = repo_manager.config.repository.build_dir
        _create_package_dir(build_dir, "pkg-a", mtime_offset=-100)
        _create_package_dir(build_dir, "pkg-b", mtime_offset=-80)
        _create_package_dir(build_dir, "pkg-c", mtime_offset=-40)
        _create_package_dir(build_dir, "pkg-d", mtime_offset=0)

        result = repo_manager._cleanup_build_dir()

        assert result == 2
        assert not (build_dir / "pkg-a").exists()
        assert not (build_dir / "pkg-b").exists()
        assert (build_dir / "pkg-c").exists()
        assert (build_dir / "pkg-d").exists()

    def test_skips_dot_prefixed(self, repo_manager):
        """Directories starting with '.' are not counted or removed."""
        repo_manager.config.retention.max_build_packages = 1
        build_dir = repo_manager.config.repository.build_dir
        _create_package_dir(build_dir, ".locks", mtime_offset=-100)
        _create_package_dir(build_dir, ".hidden", mtime_offset=-80)
        _create_package_dir(build_dir, "real-pkg", mtime_offset=0)

        result = repo_manager._cleanup_build_dir()

        assert result == 0  # 1 real pkg == max, so nothing removed
        assert (build_dir / ".locks").exists()
        assert (build_dir / ".hidden").exists()
        assert (build_dir / "real-pkg").exists()

    def test_skips_downloads_dir(self, repo_manager):
        """The 'downloads' directory is not counted or removed."""
        repo_manager.config.retention.max_build_packages = 1
        build_dir = repo_manager.config.repository.build_dir
        _create_package_dir(build_dir, "downloads", mtime_offset=-100)
        _create_package_dir(build_dir, "pkg-a", mtime_offset=-50)
        _create_package_dir(build_dir, "pkg-b", mtime_offset=0)

        result = repo_manager._cleanup_build_dir()

        assert result == 1  # pkg-a should be removed, downloads stays
        assert (build_dir / "downloads").exists()
        assert not (build_dir / "pkg-a").exists()
        assert (build_dir / "pkg-b").exists()

    def test_keeps_most_recently_modified(self, repo_manager):
        """mtime sorting determines which directories survive."""
        repo_manager.config.retention.max_build_packages = 3
        build_dir = repo_manager.config.repository.build_dir
        newest = _create_package_dir(build_dir, "pkg-new", mtime_offset=0)
        oldest = _create_package_dir(build_dir, "pkg-old", mtime_offset=-200)
        middle = _create_package_dir(build_dir, "pkg-mid", mtime_offset=-100)
        recent = _create_package_dir(build_dir, "pkg-recent", mtime_offset=-10)

        result = repo_manager._cleanup_build_dir()

        assert result == 1
        assert not oldest.exists()
        assert middle.exists()
        assert recent.exists()
        assert newest.exists()


class TestCleanup:
    """Tests for RepoManager.cleanup integration."""

    @patch.object(RepoManager, "_remove_old_packages", return_value=2)
    @patch.object(RepoManager, "_cleanup_build_dir", return_value=1)
    def test_calls_both_cleanup_methods(
        self, mock_build_cleanup, mock_repo_cleanup, repo_manager
    ):
        """cleanup() should call both _remove_old_packages and _cleanup_build_dir."""
        mock_pkg = MagicMock()
        mock_pkg.name = "test-pkg"
        with patch.object(repo_manager, "list_packages", return_value=[mock_pkg]):
            total = repo_manager.cleanup()

        assert total == 3
        mock_repo_cleanup.assert_called_once_with("test-pkg")
        mock_build_cleanup.assert_called_once_with()

    @patch.object(RepoManager, "_remove_old_packages", return_value=0)
    @patch.object(RepoManager, "_cleanup_build_dir", return_value=0)
    def test_returns_zero_when_nothing_to_clean(
        self, mock_build_cleanup, mock_repo_cleanup, repo_manager
    ):
        """cleanup() returns 0 when no cleanup is needed."""
        with patch.object(repo_manager, "list_packages", return_value=[]):
            total = repo_manager.cleanup()

        assert total == 0


class TestCleanupFullRealIntegration:
    """End-to-end test with real files in both repo and build dirs."""

    def test_cleanup_removes_old_from_both_dirs(self, mock_config):
        """cleanup() real execution: removes old repo versions AND old build dirs."""
        mock_config.retention.keep_versions = 1
        mock_config.retention.max_build_packages = 1
        manager = RepoManager(mock_config)
        repo_dir = mock_config.repository.path
        build_dir = mock_config.repository.build_dir

        # Create repo package files (2 versions of pkg-a, 1 version of pkg-b)
        _create_package_file(repo_dir, "pkg-a", "1.0-1", mtime_offset=-100)
        _create_package_file(repo_dir, "pkg-a", "2.0-1", mtime_offset=0)
        _create_package_file(repo_dir, "pkg-b", "1.0-1", mtime_offset=0)

        # Create build dirs (2 dirs, limit is 1)
        _create_package_dir(build_dir, "pkg-a", mtime_offset=-100)
        _create_package_dir(build_dir, "pkg-b", mtime_offset=0)

        total = manager.cleanup()

        assert total == 2
        # repo: pkg-a 1.0 should be removed, pkg-a 2.0 kept, pkg-b 1.0 kept
        assert not (repo_dir / "pkg-a-1.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "pkg-a-2.0-1-x86_64.pkg.tar.zst").exists()
        assert (repo_dir / "pkg-b-1.0-1-x86_64.pkg.tar.zst").exists()
        # build: pkg-a should be removed (oldest), pkg-b kept
        assert not (build_dir / "pkg-a").exists()
        assert (build_dir / "pkg-b").exists()

    def test_cleanup_removes_nothing_when_under_limits(self, mock_config):
        """cleanup() real execution: nothing removed when under both limits."""
        mock_config.retention.keep_versions = 3
        mock_config.retention.max_build_packages = 3
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

    def test_remove_old_packages_skipped_when_not_configured(self, mock_config):
        """_remove_old_packages skipped when cleanup_on_build=False."""
        mock_config.retention.cleanup_on_build = False
        manager = RepoManager(mock_config)

        with patch.object(manager, "_remove_old_packages") as mock_rm:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_rm.assert_not_called()

    def test_never_calls_cleanup_build_dir(self, mock_config):
        """_cleanup_build_dir is never called from add_packages (only via explicit cleanup)."""
        mock_config.retention.cleanup_on_build = True
        manager = RepoManager(mock_config)

        with patch.object(manager, "_cleanup_build_dir") as mock_cleanup:
            self._call_add_packages(manager, "test-pkg", "1.0-1")

        mock_cleanup.assert_not_called()

    def test_skipped_when_build_not_successful(self, mock_config):
        """Neither cleanup method called when build failed."""
        manager = RepoManager(mock_config)
        result = BuildResult(package="test-pkg", status=BuildStatus.FAILED)

        with patch.object(manager, "_cleanup_build_dir") as mock_cleanup:
            with patch.object(manager, "_remove_old_packages") as mock_rm:
                manager.add_packages(result)

        mock_cleanup.assert_not_called()
        mock_rm.assert_not_called()

    def test_skipped_when_no_artifacts(self, mock_config):
        """Neither cleanup method called when there are no artifacts."""
        manager = RepoManager(mock_config)
        result = BuildResult(package="test-pkg", status=BuildStatus.SUCCESS, artifacts=[])

        with patch.object(manager, "_cleanup_build_dir") as mock_cleanup:
            with patch.object(manager, "_remove_old_packages") as mock_rm:
                manager.add_packages(result)

        mock_cleanup.assert_not_called()
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
