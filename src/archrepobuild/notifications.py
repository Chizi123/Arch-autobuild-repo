"""Notification system with email and extensible webhook support."""

import asyncio
import shutil
import smtplib
import socket
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import aiohttp

from archrepobuild.builder import BuildResult, BuildStatus
from archrepobuild.config import Config, EmailConfig, WebhookConfig
from archrepobuild.logging import get_logger

logger = get_logger("notifications")

_GIB = 1024 ** 3


def _disk_space_warning(config: Config) -> str | None:
    """Return a low disk space warning for the repo filesystem, if needed.

    Args:
        config: Application config

    Returns:
        Warning message or None if free space is above the threshold
    """
    threshold_gib = config.notifications.email.disk_space_threshold_gb
    if not threshold_gib or threshold_gib <= 0:
        return None

    path = config.repository.path
    while not path.exists() and path != path.parent:
        path = path.parent

    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        logger.warning(f"Could not check disk space for {path}: {exc}")
        return None

    free_gib = usage.free / _GIB
    if free_gib >= threshold_gib:
        return None

    used_pct = (100.0 * usage.used / usage.total) if usage.total else 0.0
    return (
        f"WARNING: Low disk space - {free_gib:.1f} GiB free "
        f"of {usage.total / _GIB:.1f} GiB ({used_pct:.0f}% used) on {path}"
    )


@dataclass
class BuildSummary:
    """Summary of build results for notifications."""

    total: int
    success: int
    failed: int
    skipped: int
    failed_packages: list[str]
    duration: float
    timestamp: datetime

    @classmethod
    def from_results(cls, results: list[BuildResult]) -> "BuildSummary":
        """Create summary from build results."""
        return cls(
            total=len(results),
            success=sum(1 for r in results if r.status == BuildStatus.SUCCESS),
            failed=sum(1 for r in results if r.status == BuildStatus.FAILED),
            skipped=sum(1 for r in results if r.status == BuildStatus.SKIPPED),
            failed_packages=[r.package for r in results if r.status == BuildStatus.FAILED],
            duration=sum(r.duration for r in results),
            timestamp=datetime.now(),
        )


class NotificationBackend(ABC):
    """Abstract base class for notification backends."""

    @abstractmethod
    async def send(self, summary: BuildSummary, config: Config) -> bool:
        """Send notification.

        Args:
            summary: Build summary
            config: Application config

        Returns:
            True if sent successfully
        """
        pass


class EmailBackend(NotificationBackend):
    """Email notification backend."""

    def __init__(self, email_config: EmailConfig):
        """Initialize email backend.

        Args:
            email_config: Email configuration
        """
        self.config = email_config

    def _format_message(self, summary: BuildSummary, repo_name: str, disk_warning: str | None = None) -> str:
        """Format email message body.

        Args:
            summary: Build summary
            repo_name: Repository name
            disk_warning: Low disk space warning, if any

        Returns:
            Formatted message string
        """
        host = self.config.host or socket.gethostname()
        lines = [
            f"Build Report for {repo_name}",
            f"Host: {host}",
            f"Time: {summary.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Total packages: {summary.total}",
            f"  Successful: {summary.success}",
            f"  Failed: {summary.failed}",
            f"  Skipped: {summary.skipped}",
            f"Total duration: {summary.duration:.1f}s",
        ]

        if disk_warning:
            lines.extend(["", disk_warning])

        if summary.failed_packages:
            lines.extend([
                "",
                "Failed packages:",
            ])
            for pkg in summary.failed_packages:
                lines.append(f"  - {pkg}")

        return "\n".join(lines)

    async def send(self, summary: BuildSummary, config: Config) -> bool:
        """Send email notification."""
        if not self.config.enabled:
            return True

        if not self.config.to:
            logger.warning("Email notification enabled but no recipient configured")
            return False

        disk_warning = _disk_space_warning(config)

        # Only send on failures
        if (not self.config.email_everytime) and summary.failed == 0 and not disk_warning:
            logger.debug("No failures, skipping email notification")
            return True

        try:
            msg = MIMEMultipart()
            msg["From"] = self.config.from_addr or f"archrepobuild@localhost"
            msg["To"] = self.config.to
            msg["Subject"] = f"Build Errors - {config.repository.name}"

            body = self._format_message(summary, config.repository.name, disk_warning)
            msg.attach(MIMEText(body, "plain"))

            if disk_warning:
                logger.warning(disk_warning)

            # Send email
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_email, msg)

            logger.info(f"Sent email notification to {self.config.to}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def _send_email(self, msg: MIMEMultipart) -> None:
        """Send email synchronously (called from executor)."""
        smtp_class = smtplib.SMTP
        use_starttls = False

        if self.config.use_tls:
            if self.config.smtp_port == 465:
                smtp_class = smtplib.SMTP_SSL
            else:
                use_starttls = True

        with smtp_class(self.config.smtp_host, self.config.smtp_port) as server:
            if use_starttls:
                context = ssl.create_default_context()
                server.starttls(context=context)
            
            if self.config.username and self.config.password:
                server.login(self.config.username, self.config.password)
            
            server.send_message(msg)


class WebhookBackend(NotificationBackend):
    """Generic webhook notification backend.
    
    Extensible for Discord, Slack, ntfy, Gotify, etc.
    """

    def __init__(self, webhook_config: WebhookConfig):
        """Initialize webhook backend.

        Args:
            webhook_config: Webhook configuration
        """
        self.config = webhook_config

    def _format_payload(self, summary: BuildSummary, repo_name: str) -> dict[str, Any]:
        """Format webhook payload based on webhook type.

        Args:
            summary: Build summary
            repo_name: Repository name

        Returns:
            Payload dictionary
        """
        base_content = (
            f"**Build Report for {repo_name}**\n"
            f"✅ Success: {summary.success} | "
            f"❌ Failed: {summary.failed} | "
            f"⏭️ Skipped: {summary.skipped}"
        )

        if summary.failed_packages:
            base_content += f"\n\nFailed: {', '.join(summary.failed_packages)}"

        if self.config.type == "discord":
            return {
                "content": base_content,
                "embeds": [{
                    "title": f"Build Report - {repo_name}",
                    "color": 0xFF0000 if summary.failed else 0x00FF00,
                    "fields": [
                        {"name": "Success", "value": str(summary.success), "inline": True},
                        {"name": "Failed", "value": str(summary.failed), "inline": True},
                        {"name": "Skipped", "value": str(summary.skipped), "inline": True},
                    ],
                    "timestamp": summary.timestamp.isoformat(),
                }],
            }

        elif self.config.type == "slack":
            return {
                "text": base_content,
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": base_content},
                    }
                ],
            }

        elif self.config.type == "ntfy":
            return {
                "topic": repo_name,
                "title": f"Build Report - {repo_name}",
                "message": base_content,
                "priority": 5 if summary.failed else 3,
                "tags": ["package", "failed"] if summary.failed else ["package"],
            }

        else:  # generic
            return {
                "repository": repo_name,
                "summary": {
                    "total": summary.total,
                    "success": summary.success,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                },
                "failed_packages": summary.failed_packages,
                "timestamp": summary.timestamp.isoformat(),
            }

    async def send(self, summary: BuildSummary, config: Config) -> bool:
        """Send webhook notification."""
        if not self.config.enabled:
            return True

        if not self.config.url:
            logger.warning("Webhook notification enabled but no URL configured")
            return False

        # Only send on failures (configurable in future)
        if summary.failed == 0:
            logger.debug("No failures, skipping webhook notification")
            return True

        try:
            payload = self._format_payload(summary, config.repository.name)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()

            logger.info(f"Sent webhook notification to {self.config.url}")
            return True

        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")
            return False


class NotificationManager:
    """Manage multiple notification backends."""

    def __init__(self, config: Config):
        """Initialize notification manager.

        Args:
            config: Application configuration
        """
        self.config = config
        self.backends: list[NotificationBackend] = []

        # Add email backend if configured
        if config.notifications.email.enabled:
            self.backends.append(EmailBackend(config.notifications.email))

        # Add webhook backends
        for webhook in config.notifications.webhooks:
            if webhook.enabled:
                self.backends.append(WebhookBackend(webhook))

    async def notify(self, results: list[BuildResult]) -> dict[str, bool]:
        """Send notifications for build results.

        Args:
            results: List of build results

        Returns:
            Dict mapping backend type to success status
        """
        summary = BuildSummary.from_results(results)
        statuses: dict[str, bool] = {}

        for backend in self.backends:
            name = type(backend).__name__
            try:
                success = await backend.send(summary, self.config)
                statuses[name] = success
            except Exception as e:
                logger.error(f"Notification backend {name} failed: {e}")
                statuses[name] = False

        return statuses

    async def test(self) -> dict[str, bool]:
        """Send test notification through all backends.

        Returns:
            Dict mapping backend type to success status
        """
        # Create dummy test summary
        test_summary = BuildSummary(
            total=3,
            success=2,
            failed=1,
            skipped=0,
            failed_packages=["test-package"],
            duration=123.45,
            timestamp=datetime.now(),
        )

        statuses: dict[str, bool] = {}
        for backend in self.backends:
            name = type(backend).__name__
            try:
                success = await backend.send(test_summary, self.config)
                statuses[name] = success
            except Exception as e:
                logger.error(f"Test notification failed for {name}: {e}")
                statuses[name] = False

        return statuses
