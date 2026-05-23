"""Tests for Builder class, specifically package removal logic."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from archrepobuild.builder import Builder
from archrepobuild.config import Config, RepositoryConfig, BuildingConfig, SigningConfig


@pytest.fixture
def mock_config():
    """Create a real Config instance for testing."""
    return Config(
        repository=RepositoryConfig(
            name="testrepo",
            path=Path("/tmp/repo"),
            build_dir=Path("/tmp/build"),
            compression="zst",
        ),
        building=BuildingConfig(
            parallel=False,
            max_workers=1,
            clean=True,
            update_system=False,
            retry_attempts=2,
        ),
        signing=SigningConfig(
            enabled=False,
        ),
    )


@pytest.fixture
def mock_aur_client():
    """Create a mock AURClient."""
    return MagicMock()


class TestBuilderGetBuiltPackages:
    """Tests for Builder.get_built_packages."""

    def test_get_built_packages_no_pkgbuild(self, mock_config, mock_aur_client):
        """Test get_built_packages when PKGBUILD does not exist."""
        builder = Builder(mock_config, mock_aur_client)

        with patch("pathlib.Path.exists", return_value=False):
            packages = builder.get_built_packages("test-pkg")
            assert packages == ["test-pkg"]

    @patch("archrepobuild.builder.subprocess.run")
    def test_get_built_packages_single_package(self, mock_run, mock_config, mock_aur_client):
        """Test get_built_packages with a single package PKGBUILD."""
        builder = Builder(mock_config, mock_aur_client)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "/tmp/build/test-pkg/test-pkg-1.0-1-any.pkg.tar.zst\n"

        with patch("pathlib.Path.exists", return_value=True):
            packages = builder.get_built_packages("test-pkg")
            assert packages == ["test-pkg"]
            mock_run.assert_called_once_with(
                ["makepkg", "--packagelist"],
                cwd=Path("/tmp/build/test-pkg"),
                capture_output=True,
                text=True,
                check=True,
            )

    @patch("archrepobuild.builder.subprocess.run")
    def test_get_built_packages_split_package(self, mock_run, mock_config, mock_aur_client):
        """Test get_built_packages with a split package PKGBUILD."""
        builder = Builder(mock_config, mock_aur_client)

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "/tmp/build/my-split-app/my-split-app-client-1.0-1-any.pkg.tar.zst\n"
            "/tmp/build/my-split-app/my-split-app-server-2.0-1-any.pkg.tar.zst\n"
        )

        with patch("pathlib.Path.exists", return_value=True):
            packages = builder.get_built_packages("my-split-app")
            assert packages == ["my-split-app-client", "my-split-app-server"]

    @patch("archrepobuild.builder.subprocess.run")
    def test_get_built_packages_failure_fallback(self, mock_run, mock_config, mock_aur_client):
        """Test get_built_packages falls back to package name on failure."""
        builder = Builder(mock_config, mock_aur_client)

        mock_run.side_effect = Exception("Subprocess error")

        with patch("pathlib.Path.exists", return_value=True):
            packages = builder.get_built_packages("my-app")
            assert packages == ["my-app"]
