"""Tests for RepoManager, specifically build directory cleanup."""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


class TestCleanupBuildDirFullIntegration:
    """End-to-end tests exercising the real _cleanup_build_dir logic."""

    def test_real_cleanup_via_cleanup_command(self, mock_config):
        """Verify cleanup() calls the real _cleanup_build_dir with real dirs."""
        mock_config.retention.max_build_packages = 2
        manager = RepoManager(mock_config)
        build_dir = mock_config.repository.build_dir

        _create_package_dir(build_dir, "pkg-a", mtime_offset=-100)
        _create_package_dir(build_dir, "pkg-b", mtime_offset=0)

        with patch.object(manager, "list_packages", return_value=[]):
            with patch.object(manager, "_remove_old_packages", return_value=0):
                total = manager.cleanup()

        assert total == 0  # 2 dirs == max_build_packages, nothing removed
        assert (build_dir / "pkg-a").exists()
        assert (build_dir / "pkg-b").exists()
