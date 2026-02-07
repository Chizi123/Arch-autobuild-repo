"""Tests for AUR client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from archbuild.aur import AURClient, Package


@pytest.fixture
def sample_package_data():
    """Sample AUR RPC response data."""
    return {
        "Name": "test-package",
        "Version": "1.0.0-1",
        "Description": "A test package",
        "URL": "https://example.com",
        "Maintainer": "testuser",
        "NumVotes": 100,
        "Popularity": 1.5,
        "OutOfDate": None,
        "FirstSubmitted": 1609459200,
        "LastModified": 1640995200,
        "Depends": ["dep1", "dep2"],
        "MakeDepends": ["makedep1"],
        "CheckDepends": [],
        "OptDepends": [],
        "Provides": [],
        "Conflicts": [],
        "Replaces": [],
        "License": ["MIT"],
        "Keywords": ["test"],
    }


class TestPackage:
    """Tests for Package dataclass."""

    def test_from_rpc(self, sample_package_data):
        """Test creating Package from RPC data."""
        pkg = Package.from_rpc(sample_package_data)

        assert pkg.name == "test-package"
        assert pkg.version == "1.0.0-1"
        assert pkg.description == "A test package"
        assert pkg.maintainer == "testuser"
        assert pkg.votes == 100
        assert pkg.depends == ["dep1", "dep2"]
        assert pkg.makedepends == ["makedep1"]

    def test_git_url(self, sample_package_data):
        """Test git_url property."""
        pkg = Package.from_rpc(sample_package_data)
        assert pkg.git_url == "https://aur.archlinux.org/test-package.git"

    def test_aur_url(self, sample_package_data):
        """Test aur_url property."""
        pkg = Package.from_rpc(sample_package_data)
        assert pkg.aur_url == "https://aur.archlinux.org/packages/test-package"


class TestAURClient:
    """Tests for AURClient."""

    @pytest.mark.asyncio
    async def test_get_package_cached(self, sample_package_data):
        """Test that cached packages are returned."""
        async with AURClient(cache_ttl=300) as client:
            # Manually add to cache
            pkg = Package.from_rpc(sample_package_data)
            client._set_cached(pkg)

            # Should return from cache without network request
            result = await client.get_package("test-package")
            assert result is not None
            assert result.name == "test-package"

    @pytest.mark.asyncio
    async def test_cache_expiry(self, sample_package_data):
        """Test cache TTL expiry."""
        async with AURClient(cache_ttl=0) as client:
            pkg = Package.from_rpc(sample_package_data)
            client._set_cached(pkg)

            # Cache should be expired immediately
            assert client._get_cached("test-package") is None

    @pytest.mark.asyncio
    async def test_batch_request(self, sample_package_data):
        """Test batch package requests."""
        async with AURClient() as client:
            # Mock the request method
            mock_response = {
                "type": "multiinfo",
                "results": [sample_package_data],
            }
            client._request = AsyncMock(return_value=mock_response)

            packages = await client.get_packages(["test-package"])

            assert len(packages) == 1
            assert packages[0].name == "test-package"


class TestDependencyParsing:
    """Tests for dependency string parsing."""

    def test_parse_simple(self):
        """Test parsing simple dependency."""
        from archbuild.resolver import Dependency
        dep = Dependency.parse("package")
        assert dep.name == "package"
        assert dep.version_constraint is None

    def test_parse_with_version(self):
        """Test parsing dependency with version."""
        from archbuild.resolver import Dependency
        dep = Dependency.parse("package>=1.0")
        assert dep.name == "package"
        assert dep.version_constraint == ">=1.0"

    def test_parse_exact_version(self):
        """Test parsing dependency with exact version."""
        from archbuild.resolver import Dependency
        dep = Dependency.parse("package=2.0")
        assert dep.name == "package"
        assert dep.version_constraint == "=2.0"
