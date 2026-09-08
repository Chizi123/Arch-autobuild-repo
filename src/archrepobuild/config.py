"""Configuration loading and validation using Pydantic."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class RepositoryConfig(BaseModel):
    """Repository settings."""

    name: str = Field(..., description="Repository name")
    path: Path = Field(..., description="Path to repository directory")
    build_dir: Path = Field(..., description="Path to build directory")
    compression: str = Field(default="zst", description="Compression format")


class BuildingConfig(BaseModel):
    """Build settings."""

    parallel: bool = Field(default=True, description="Enable parallel builds")
    max_workers: int = Field(default=4, ge=0, description="Maximum parallel workers (0 = number of CPUs)")
    clean: bool = Field(default=True, description="Clean build directory after build")
    update_system: bool = Field(default=False, description="Update system before building")
    clear_pacman_cache: bool = Field(default=False, description="Clear pacman package cache after system update")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Retry attempts on failure")
    retry_delay: int = Field(default=5, ge=1, description="Base delay between retries (seconds)")
    reboot_on_critical_updates: bool = Field(default=False, description="Reboot system if critical packages are updated")
    critical_packages: list[str] = Field(
        default_factory=lambda: [
            "linux", "linux-lts", "linux-zen", "linux-hardened", "systemd", "glibc",
            "intel-ucode", "amd-ucode", "nvidia", "nvidia-lts", "nvidia-dkms",
            "mesa", "openssl", "grub", "sudo", "pacman"
        ],
        description="List of critical packages that trigger a reboot on update"
    )


class SigningConfig(BaseModel):
    """Package signing settings."""

    enabled: bool = Field(default=False, description="Enable package signing")
    key: str = Field(default="", description="GPG key ID for signing")


class EmailConfig(BaseModel):
    """Email notification settings."""

    enabled: bool = Field(default=False, description="Enable email notifications")
    email_everytime: bool = Field(default=False, description="Send email every time the script is run")
    to: str = Field(default="", description="Recipient email address")
    from_addr: str = Field(default="", alias="from", description="Sender email address")
    host: str = Field(default="", description="Hostname to identify the build machine in notifications")
    smtp_host: str = Field(default="localhost", description="SMTP server host")
    smtp_port: int = Field(default=25, description="SMTP server port")
    use_tls: bool = Field(default=False, description="Use TLS for SMTP")
    username: str = Field(default="", description="SMTP username")
    password: str = Field(default="", description="SMTP password")


class WebhookConfig(BaseModel):
    """Webhook notification settings (extensible for future use)."""

    enabled: bool = Field(default=False, description="Enable webhook notifications")
    url: str = Field(default="", description="Webhook URL")
    type: str = Field(default="generic", description="Webhook type (generic, discord, slack)")


class NotificationsConfig(BaseModel):
    """Notification settings."""

    email: EmailConfig = Field(default_factory=EmailConfig)
    webhooks: list[WebhookConfig] = Field(default_factory=list)


class PackageRetentionConfig(BaseModel):
    """Package retention settings."""

    keep_versions: int = Field(default=3, ge=1, le=100, description="Number of old versions to keep in repo and build dir")
    cleanup_on_build: bool = Field(default=True, description="Clean old versions after build")
    max_build_packages: int = Field(default=0, ge=0, description="Maximum number of package directories in build dir (0 = no limit)")


class PackageOverride(BaseModel):
    """Per-package build overrides."""

    skip_checksums: bool = Field(default=False, description="Skip checksum verification")
    extra_args: list[str] = Field(default_factory=list, description="Extra makepkg arguments")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")


class Config(BaseModel):
    """Main configuration model."""

    repository: RepositoryConfig
    building: BuildingConfig = Field(default_factory=BuildingConfig)
    signing: SigningConfig = Field(default_factory=SigningConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    retention: PackageRetentionConfig = Field(default_factory=PackageRetentionConfig)
    package_overrides: dict[str, PackageOverride] = Field(
        default_factory=dict, description="Per-package overrides"
    )
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: Path | None = Field(default=None, description="Optional log file path")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v.upper()


def load_config(path: Path) -> Config:
    """Load and validate configuration from YAML file.

    Args:
        path: Path to configuration file

    Returns:
        Validated Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValidationError: If config is invalid
    """
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    return Config.model_validate(data)


def migrate_vars_sh(vars_path: Path) -> dict[str, Any]:
    """Migrate old vars.sh to new config format.

    Args:
        vars_path: Path to vars.sh file

    Returns:
        Dictionary suitable for Config.model_validate()
    """
    variables: dict[str, str] = {}

    with open(vars_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                # Handle export statements
                if line.startswith("export "):
                    line = line[7:]
                key, _, value = line.partition("=")
                # Remove quotes
                value = value.strip("'\"")
                variables[key] = value

    # Convert to new format
    config: dict[str, Any] = {
        "repository": {
            "name": variables.get("REPONAME", ""),
            "path": variables.get("REPODIR", "/repo/x86_64"),
            "build_dir": variables.get("BUILDDIR", "/repo/build"),
            "compression": variables.get("COMPRESSION", "zst"),
        },
        "building": {
            "parallel": variables.get("PARALLEL", "N") == "Y",
            "clean": variables.get("CLEAN", "N") == "Y",
            "update_system": variables.get("UPDATE", "N") == "Y",
        },
        "signing": {
            "enabled": variables.get("SIGN", "N") == "Y",
            "key": variables.get("KEY", ""),
        },
        "notifications": {
            "email": {
                "enabled": bool(variables.get("TO_EMAIL")),
                "to": variables.get("TO_EMAIL", ""),
                "from": variables.get("FROM_EMAIL", ""),
            }
        },
        "retention": {
            "keep_versions": int(variables.get("NUM_OLD", "5")),
        },
    }

    return config


def save_config(config: Config, path: Path) -> None:
    """Save configuration to YAML file.

    Args:
        config: Config object to save
        path: Path to save configuration file
    """
    data = config.model_dump(by_alias=True, exclude_none=True)
    
    # Convert Path objects to strings for YAML
    def convert_paths(obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: convert_paths(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_paths(v) for v in obj]
        return obj

    data = convert_paths(data)

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
