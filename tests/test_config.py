"""Tests for configuration loading and validation."""

import pytest
from pathlib import Path
from tempfile import NamedTemporaryFile

from archbuild.config import (
    Config,
    load_config,
    migrate_vars_sh,
    save_config,
    BuildingConfig,
    RepositoryConfig,
)


class TestConfig:
    """Tests for Config model."""

    def test_minimal_config(self):
        """Test minimal valid configuration."""
        config = Config(
            repository=RepositoryConfig(
                name="test",
                path=Path("/repo"),
                build_dir=Path("/build"),
            )
        )
        assert config.repository.name == "test"
        assert config.building.parallel is True  # default
        assert config.building.max_workers == 4  # default

    def test_building_config_defaults(self):
        """Test BuildingConfig defaults."""
        config = BuildingConfig()
        assert config.parallel is True
        assert config.max_workers == 4
        assert config.clean is True
        assert config.retry_attempts == 3

    def test_max_workers_validation(self):
        """Test max_workers bounds."""
        with pytest.raises(ValueError):
            BuildingConfig(max_workers=0)
        with pytest.raises(ValueError):
            BuildingConfig(max_workers=100)

    def test_log_level_validation(self):
        """Test log level validation."""
        config = Config(
            repository=RepositoryConfig(
                name="test",
                path=Path("/repo"),
                build_dir=Path("/build"),
            ),
            log_level="debug",
        )
        assert config.log_level == "DEBUG"

        with pytest.raises(ValueError):
            Config(
                repository=RepositoryConfig(
                    name="test",
                    path=Path("/repo"),
                    build_dir=Path("/build"),
                ),
                log_level="invalid",
            )


class TestLoadConfig:
    """Tests for config file loading."""

    def test_load_yaml(self, tmp_path):
        """Test loading YAML config file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repository:
  name: myrepo
  path: /repo/x86_64
  build_dir: /repo/build
building:
  max_workers: 8
""")
        config = load_config(config_file)
        assert config.repository.name == "myrepo"
        assert config.building.max_workers == 8

    def test_file_not_found(self):
        """Test error on missing config file."""
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.yaml"))


class TestMigrateVarsSh:
    """Tests for vars.sh migration."""

    def test_migrate_basic(self, tmp_path):
        """Test basic vars.sh migration."""
        vars_file = tmp_path / "vars.sh"
        vars_file.write_text("""
REPODIR=/repo/x86_64
BUILDDIR=/repo/build
REPONAME=myrepo
PARALLEL=Y
SIGN=N
NUM_OLD=5
""")
        data = migrate_vars_sh(vars_file)

        assert data["repository"]["name"] == "myrepo"
        assert data["repository"]["path"] == "/repo/x86_64"
        assert data["building"]["parallel"] is True
        assert data["signing"]["enabled"] is False
        assert data["retention"]["keep_versions"] == 5

    def test_migrate_with_export(self, tmp_path):
        """Test migration handles export statements."""
        vars_file = tmp_path / "vars.sh"
        vars_file.write_text("""
export REPONAME="testrepo"
export REPODIR="/test/repo"
export BUILDDIR="/test/build"
""")
        data = migrate_vars_sh(vars_file)

        assert data["repository"]["name"] == "testrepo"


class TestSaveConfig:
    """Tests for config saving."""

    def test_round_trip(self, tmp_path):
        """Test config save/load round trip."""
        config = Config(
            repository=RepositoryConfig(
                name="test",
                path=Path("/repo"),
                build_dir=Path("/build"),
            ),
            building=BuildingConfig(max_workers=6),
        )

        config_file = tmp_path / "config.yaml"
        save_config(config, config_file)

        loaded = load_config(config_file)
        assert loaded.repository.name == "test"
        assert loaded.building.max_workers == 6
