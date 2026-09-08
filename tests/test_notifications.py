"""Tests for the notification system, including disk space warnings."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from archrepobuild.builder import BuildResult, BuildStatus
from archrepobuild.config import (
    BuildingConfig,
    Config,
    EmailConfig,
    NotificationsConfig,
    RepositoryConfig,
    SigningConfig,
)
from archrepobuild.notifications import (
    BuildSummary,
    EmailBackend,
    NotificationManager,
    _disk_space_warning,
)

_GIB = 1024 ** 3


@pytest.fixture
def mock_config(tmp_path):
    """Create a Config with a tmp repo path and testable email settings."""
    return Config(
        repository=RepositoryConfig(
            name="testrepo",
            path=tmp_path / "repo",
            build_dir=tmp_path / "build",
            compression="zst",
        ),
        building=BuildingConfig(parallel=False, max_workers=1, clean=True),
        signing=SigningConfig(enabled=False),
        notifications=NotificationsConfig(
            email=EmailConfig(
                enabled=True,
                email_everytime=False,
                to="test@example.com",
                smtp_host="localhost",
            )
        ),
    )


def _diskusage(total, used, free):
    return Mock(total=total, used=used, free=free)


@pytest.mark.parametrize(
    "usage,threshold,expected",
    [
        (_diskusage(50 * _GIB, 40 * _GIB, 10 * _GIB), 5, None),
        (_diskusage(50 * _GIB, 40 * _GIB, 10 * _GIB), 15, "WARNING: Low disk space"),
        (_diskusage(50 * _GIB, 49.5 * _GIB, 0.5 * _GIB), 1, "WARNING: Low disk space"),
        (_diskusage(50 * _GIB, 40 * _GIB, 10 * _GIB), 0, None),
    ],
)
def test_disk_space_warning(mock_config, usage, threshold, expected):
    mock_config.notifications.email.disk_space_threshold_gb = threshold
    mock_config.repository.path.mkdir(parents=True)
    with patch("archrepobuild.notifications.shutil.disk_usage", return_value=usage):
        warning = _disk_space_warning(mock_config)

    if expected is None:
        assert warning is None
    else:
        assert warning is not None
        assert expected in warning
        assert "GiB free" in warning


def test_disk_space_warning_uses_repo_path(mock_config):
    mock_config.notifications.email.disk_space_threshold_gb = 10
    mock_config.repository.path.mkdir(parents=True)
    with patch(
        "archrepobuild.notifications.shutil.disk_usage",
        return_value=_diskusage(50 * _GIB, 40 * _GIB, 10 * _GIB),
    ) as mock_usage:
        _disk_space_warning(mock_config)

    mock_usage.assert_called_once()
    assert mock_usage.call_args[0][0].endswith("repo")


def test_format_message_includes_disk_warning(mock_config):
    backend = EmailBackend(mock_config.notifications.email)
    summary = BuildSummary(
        total=1, success=1, failed=0, skipped=0, failed_packages=[], duration=1.0,
        timestamp=datetime.now(),
    )

    body = backend._format_message(
        summary, "testrepo", "WARNING: Low disk space - 2.0 GiB free"
    )

    assert "WARNING: Low disk space - 2.0 GiB free" in body


def test_format_message_without_disk_warning(mock_config):
    backend = EmailBackend(mock_config.notifications.email)
    summary = BuildSummary(
        total=1, success=1, failed=0, skipped=0, failed_packages=["bad-pkg"], duration=1.0,
        timestamp=datetime.now(),
    )

    body = backend._format_message(summary, "testrepo")

    assert "Low disk space" not in body
    assert "bad-pkg" in body


def test_send_skipped_when_no_failures_and_no_warning(mock_config):
    backend = EmailBackend(mock_config.notifications.email)
    summary = BuildSummary(
        total=1, success=1, failed=0, skipped=0, failed_packages=[], duration=1.0,
        timestamp=datetime.now(),
    )
    with patch.object(backend, "_send_email") as mock_send:
        result = asyncio.run(backend.send(summary, mock_config))

    assert result is True
    mock_send.assert_not_called()


def test_send_sent_when_low_disk_even_without_failures(mock_config):
    mock_config.notifications.email.disk_space_threshold_gb = 5
    mock_config.repository.path.mkdir(parents=True)
    backend = EmailBackend(mock_config.notifications.email)
    summary = BuildSummary(
        total=1, success=1, failed=0, skipped=0, failed_packages=[], duration=1.0,
        timestamp=datetime.now(),
    )
    with patch(
        "archrepobuild.notifications.shutil.disk_usage",
        return_value=_diskusage(50 * _GIB, 49 * _GIB, 1 * _GIB),
    ):
        with patch.object(backend, "_send_email") as mock_send:
            result = asyncio.run(backend.send(summary, mock_config))

    assert result is True
    mock_send.assert_called_once()


def test_notification_manager_builds_emails(mock_config):
    manager = NotificationManager(mock_config)
    assert len(manager.backends) == 1
    assert isinstance(manager.backends[0], EmailBackend)

    results = [BuildResult(package="pkg", status=BuildStatus.SUCCESS)]
    statuses = asyncio.run(manager.notify(results))

    # Sends email only on failures by default; here no failure -> skipped
    assert statuses["EmailBackend"] is True