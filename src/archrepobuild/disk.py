"""Shared disk space helpers."""

import shutil
from typing import Any

from archrepobuild.config import Config
from archrepobuild.logging import get_logger

logger = get_logger("disk")

GIB = 1024 ** 3


def resolved_repo_path(config: Config):
    """Return the nearest existing ancestor of the configured repo path."""
    path = config.repository.path
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def repo_disk_usage(config: Config) -> Any | None:
    """Return disk usage for the repo filesystem, or None on error.

    Walks up from the configured repository path to the nearest existing
    directory so the check works even if the path does not exist yet.
    """
    path = resolved_repo_path(config)
    try:
        return shutil.disk_usage(str(path))
    except OSError as exc:
        logger.warning(f"Could not check disk space for {path}: {exc}")
        return None