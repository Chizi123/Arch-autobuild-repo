"""Unit tests for verbose mode."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from click.testing import CliRunner

from archrepobuild.builder import Builder, _run_makepkg
from archrepobuild.cli import cli, Context
from archrepobuild.config import Config, RepositoryConfig, BuildingConfig, SigningConfig


def test_builder_verbose_init():
    """Verify that Builder initializes the verbose attribute correctly."""
    mock_config = MagicMock(spec=Config)
    mock_config.repository = MagicMock(spec=RepositoryConfig)
    mock_config.repository.build_dir = Path("/tmp/build")
    mock_config.building = MagicMock(spec=BuildingConfig)
    mock_config.building.parallel = False

    mock_aur = MagicMock()

    builder_default = Builder(mock_config, mock_aur)
    assert not builder_default.verbose

    builder_verbose = Builder(mock_config, mock_aur, verbose=True)
    assert builder_verbose.verbose


@patch("archrepobuild.cli.setup_logging")
@patch("archrepobuild.cli.load_config")
@patch("archrepobuild.cli.AURClient")
@patch("archrepobuild.cli.Builder")
def test_cli_verbose_flag_parsing(mock_builder, mock_aur_client, mock_load_config, mock_setup_logging):
    """Verify that verbose flags on CLI propagate correctly and set logging to DEBUG."""
    # Set up mock config
    mock_config = MagicMock(spec=Config)
    mock_config.log_level = "INFO"
    mock_config.log_file = None
    mock_load_config.return_value = mock_config

    # Set up mock builder context manager
    mock_builder.return_value.__aenter__.return_value = MagicMock()

    runner = CliRunner()

    # Test global verbose flag: archrepobuild -v buildpkg pkgname
    with patch("archrepobuild.cli.run_async") as mock_run_async:
        result = runner.invoke(cli, ["-v", "build", "mypackage"])
        assert result.exit_code == 0
        mock_setup_logging.assert_called_with("DEBUG", None)

    mock_setup_logging.reset_mock()

    # Test local command verbose flag: archrepobuild build pkgname -v
    with patch("archrepobuild.cli.run_async") as mock_run_async:
        result = runner.invoke(cli, ["build", "mypackage", "-v"])
        assert result.exit_code == 0
        mock_setup_logging.assert_called_with("DEBUG", None)

    mock_setup_logging.reset_mock()

    # Test add command verbose flag: archrepobuild add pkgname -v
    with patch("archrepobuild.cli.run_async") as mock_run_async:
        result = runner.invoke(cli, ["add", "mypackage", "-v"])
        assert result.exit_code == 0
        mock_setup_logging.assert_called_with("DEBUG", None)


@patch("archrepobuild.builder.subprocess.Popen")
@patch("sys.stdout.write")
def test_run_makepkg_verbose(mock_stdout_write, mock_popen):
    """Verify _run_makepkg streams output to stdout in verbose mode."""
    # Set up mock Popen process
    mock_process = MagicMock()
    mock_process.stdout.readline.side_effect = ["line 1\n", "line 2\n", ""]
    mock_process.poll.return_value = 0
    mock_process.returncode = 1
    mock_popen.return_value = mock_process

    package_dir = Path("/tmp/mypkg")

    success, error, artifacts = _run_makepkg(
        package_dir=package_dir,
        sign=False,
        clean=True,
        force=False,
        skip_checksums=False,
        verbose=True,
    )

    assert not success
    assert error == "line 1\nline 2\n"
    assert artifacts == []

    # Verify Popen was called
    mock_popen.assert_called_once()
    assert mock_popen.call_args[0][0] == ["makepkg", "-s", "--noconfirm", "-c"]
    assert mock_popen.call_args[1]["cwd"] == package_dir

    # Verify sys.stdout.write was called with each line
    mock_stdout_write.assert_has_calls([
        call("line 1\n"),
        call("line 2\n"),
    ])


@patch("archrepobuild.builder.subprocess.run")
def test_run_makepkg_non_verbose(mock_sub_run):
    """Verify _run_makepkg uses subprocess.run when verbose is False."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "build successful"
    mock_result.stderr = ""
    mock_sub_run.return_value = mock_result

    package_dir = Path("/tmp/mypkg")

    success, error, artifacts = _run_makepkg(
        package_dir=package_dir,
        sign=False,
        clean=True,
        force=False,
        skip_checksums=False,
        verbose=False,
    )

    assert success
    assert error == ""
    assert artifacts == []

    # Verify subprocess.run was called, NOT Popen
    mock_sub_run.assert_called_once()
