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


class TestBuilderBuildAllReboot:
    """Tests for system update reboot logic in Builder.build_all."""

    @pytest.mark.asyncio
    @patch("archrepobuild.builder.subprocess.run")
    async def test_build_all_no_reboot_on_no_changes(self, mock_run, mock_config, mock_aur_client):
        """Test build_all does not reboot if critical packages do not change."""
        mock_config.building.update_system = True
        mock_config.building.reboot_on_critical_updates = True
        mock_config.building.critical_packages = ["linux", "systemd"]

        mock_res_linux_before = MagicMock()
        mock_res_linux_before.returncode = 0
        mock_res_linux_before.stdout = "linux 6.1.0-1-arch\n"

        mock_res_systemd_before = MagicMock()
        mock_res_systemd_before.returncode = 0
        mock_res_systemd_before.stdout = "systemd 253-1\n"

        mock_res_update = MagicMock()
        mock_res_update.returncode = 0

        mock_res_linux_after = MagicMock()
        mock_res_linux_after.returncode = 0
        mock_res_linux_after.stdout = "linux 6.1.0-1-arch\n"

        mock_res_systemd_after = MagicMock()
        mock_res_systemd_after.returncode = 0
        mock_res_systemd_after.stdout = "systemd 253-1\n"

        mock_run.side_effect = [
            mock_res_linux_before,
            mock_res_systemd_before,
            mock_res_update,
            mock_res_linux_after,
            mock_res_systemd_after,
        ]

        builder = Builder(mock_config, mock_aur_client)
        with patch("pathlib.Path.iterdir", return_value=[]):
            results = await builder.build_all()
            assert results == []

        assert not any("reboot" in str(arg) for arg in mock_run.call_args_list)

    @pytest.mark.asyncio
    @patch("archrepobuild.builder.subprocess.run")
    @patch("sys.exit")
    async def test_build_all_reboots_on_changes(self, mock_exit, mock_run, mock_config, mock_aur_client):
        """Test build_all triggers reboot and exits if a critical package changes version."""
        mock_config.building.update_system = True
        mock_config.building.reboot_on_critical_updates = True
        mock_config.building.critical_packages = ["linux"]

        mock_res_before = MagicMock()
        mock_res_before.returncode = 0
        mock_res_before.stdout = "linux 6.1.0-1\n"

        mock_res_update = MagicMock()
        mock_res_update.returncode = 0

        mock_res_after = MagicMock()
        mock_res_after.returncode = 0
        mock_res_after.stdout = "linux 6.2.0-1\n"

        mock_res_reboot = MagicMock()
        mock_res_reboot.returncode = 0

        mock_run.side_effect = [
            mock_res_before,
            mock_res_update,
            mock_res_after,
            mock_res_reboot,
        ]

        mock_exit.side_effect = SystemExit

        builder = Builder(mock_config, mock_aur_client)

        with pytest.raises(SystemExit):
            await builder.build_all()

        mock_run.assert_any_call(["sudo", "reboot"], check=False)
        mock_exit.assert_called_once_with(0)
