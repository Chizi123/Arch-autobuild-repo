"""Tests for dependency resolver."""

import pytest
from unittest.mock import AsyncMock, patch

from archrepobuild.resolver import DependencyResolver, Dependency, DependencyType, BuildOrder


class TestDependency:
    """Tests for Dependency class."""

    def test_parse_simple(self):
        """Test parsing simple dependency name."""
        dep = Dependency.parse("packagename")
        assert dep.name == "packagename"
        assert dep.version_constraint is None
        assert dep.dep_type == DependencyType.RUNTIME

    def test_parse_with_gte(self):
        """Test parsing with >= constraint."""
        dep = Dependency.parse("package>=1.0")
        assert dep.name == "package"
        assert dep.version_constraint == ">=1.0"

    def test_parse_with_lte(self):
        """Test parsing with <= constraint."""
        dep = Dependency.parse("package<=2.0")
        assert dep.name == "package"
        assert dep.version_constraint == "<=2.0"

    def test_parse_build_dep(self):
        """Test parsing as build dependency."""
        dep = Dependency.parse("makedep", DependencyType.BUILD)
        assert dep.dep_type == DependencyType.BUILD


class TestBuildOrder:
    """Tests for BuildOrder class."""

    def test_iteration(self):
        """Test BuildOrder iteration."""
        order = BuildOrder(packages=["a", "b", "c"])
        assert list(order) == ["a", "b", "c"]

    def test_length(self):
        """Test BuildOrder length."""
        order = BuildOrder(packages=["a", "b"])
        assert len(order) == 2


class TestDependencyResolver:
    """Tests for DependencyResolver."""

    @pytest.fixture
    def mock_aur_client(self):
        """Create mock AUR client."""
        client = AsyncMock()
        return client

    def test_topological_sort_simple(self, mock_aur_client):
        """Test topological sort with simple graph."""
        resolver = DependencyResolver(mock_aur_client)
        
        # A depends on B, B depends on C
        graph = {
            "A": {"B"},
            "B": {"C"},
            "C": set(),
        }

        order = resolver._topological_sort(graph)
        
        # C must come before B, B must come before A
        assert order.index("C") < order.index("B")
        assert order.index("B") < order.index("A")

    def test_topological_sort_parallel(self, mock_aur_client):
        """Test topological sort with parallel dependencies."""
        resolver = DependencyResolver(mock_aur_client)
        
        # A depends on B and C (parallel)
        graph = {
            "A": {"B", "C"},
            "B": set(),
            "C": set(),
        }

        order = resolver._topological_sort(graph)
        
        # B and C must come before A
        assert order.index("B") < order.index("A")
        assert order.index("C") < order.index("A")

    def test_detect_cycles_no_cycle(self, mock_aur_client):
        """Test cycle detection with no cycles."""
        resolver = DependencyResolver(mock_aur_client)
        
        graph = {
            "A": {"B"},
            "B": {"C"},
            "C": set(),
        }

        cycles = resolver.detect_cycles(graph)
        assert len(cycles) == 0

    def test_detect_cycles_with_cycle(self, mock_aur_client):
        """Test cycle detection with cycle."""
        resolver = DependencyResolver(mock_aur_client)
        
        # A -> B -> C -> A (cycle)
        graph = {
            "A": {"B"},
            "B": {"C"},
            "C": {"A"},
        }

        cycles = resolver.detect_cycles(graph)
        assert len(cycles) > 0

    @patch("archrepobuild.resolver.subprocess.run")
    def test_is_in_repos(self, mock_run, mock_aur_client):
        """Test checking repos."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "core base\nextra git\ncustom mypkg\n"

        resolver = DependencyResolver(mock_aur_client)
        
        # Test default (include_all=True)
        assert resolver.is_in_repos("git")
        assert resolver.is_in_repos("mypkg")
        assert resolver.is_in_repos("base")
        assert not resolver.is_in_repos("yay")

        # Test official_only (include_all=False)
        assert resolver.is_in_repos("git", include_all=False)
        assert resolver.is_in_repos("base", include_all=False)
        assert not resolver.is_in_repos("mypkg", include_all=False)

    @pytest.mark.asyncio
    async def test_resolve_includes_checkdepends(self, mock_aur_client):
        """Test that resolve includes checkdepends in the build order or dependency map."""
        from archrepobuild.aur import Package
        
        resolver = DependencyResolver(mock_aur_client)
        
        # Mock AUR response
        from datetime import datetime
        pkg = Package(
            name="test-pkg",
            version="1.0",
            description="test",
            url=None,
            maintainer=None,
            votes=0,
            popularity=0.0,
            out_of_date=None,
            first_submitted=datetime.now(),
            last_modified=datetime.now(),
            package_base="test-pkg",
            depends=[],
            makedepends=[],
            checkdepends=["check-dep"],
        )
        
        dep_pkg = Package(
            name="check-dep",
            version="1.0",
            description="test",
            url=None,
            maintainer=None,
            votes=0,
            popularity=0.0,
            out_of_date=None,
            first_submitted=datetime.now(),
            last_modified=datetime.now(),
            package_base="check-dep",
            depends=[],
            makedepends=[],
            checkdepends=[],
        )
        
        mock_aur_client.get_packages.side_effect = [[pkg], [dep_pkg]]
        
        # Assume neither is in repos or installed
        with patch.object(resolver, "is_in_repos", return_value=False), \
             patch.object(resolver, "is_installed", return_value=False):
            
            build_order = await resolver.resolve(["test-pkg"])
            
            assert "check-dep" in build_order.packages
            assert "check-dep" in [d.name for d in build_order.aur_dependencies["test-pkg"]]
            assert any(d.dep_type == DependencyType.CHECK for d in build_order.aur_dependencies["test-pkg"])

    @pytest.mark.asyncio
    async def test_resolve_with_exclude_repo(self, mock_aur_client):
        """Test that resolve correctly handles exclude_repo."""
        from archrepobuild.aur import Package
        
        resolver = DependencyResolver(mock_aur_client)
        
        from datetime import datetime
        # Mock AUR response
        pkg = Package(
            name="test-pkg",
            version="1.0",
            description="test",
            url=None,
            maintainer=None,
            votes=0,
            popularity=0.0,
            out_of_date=None,
            first_submitted=datetime.now(),
            last_modified=datetime.now(),
            package_base="test-pkg",
            depends=[],
            makedepends=[],
            checkdepends=[],
        )
        
        mock_aur_client.get_packages.return_value = [pkg]
        
        # Case 1: Package in repo, not excluded -> should be skipped
        with patch.object(resolver, "is_in_repos", return_value="myrepo"):
            build_order = await resolver.resolve(["test-pkg"])
            assert "test-pkg" not in build_order.packages
            
        # Case 2: Package in repo, excluded -> should be included (AUR lookup)
        with patch.object(resolver, "is_in_repos", return_value="myrepo"):
            build_order = await resolver.resolve(["test-pkg"], exclude_repo="myrepo")
            assert "test-pkg" in build_order.packages

        # Case 3: Package in different repo, not excluded -> should be skipped
        with patch.object(resolver, "is_in_repos", return_value="otherrepo"):
            build_order = await resolver.resolve(["test-pkg"], exclude_repo="myrepo")
            assert "test-pkg" not in build_order.packages
