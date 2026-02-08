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
    def test_is_in_official_repos(self, mock_run, mock_aur_client):
        """Test checking official repos."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "base\ngit\nvim\n"

        resolver = DependencyResolver(mock_aur_client)
        resolver._refresh_pacman_cache()

        assert resolver.is_in_official_repos("git")
        assert not resolver.is_in_official_repos("yay")
