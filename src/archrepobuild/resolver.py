"""Dependency resolution with topological sorting."""

import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from archrepobuild.aur import AURClient, Package
from archrepobuild.logging import get_logger

logger = get_logger("resolver")

# Official Arch Linux repositories
OFFICIAL_REPOS = {"core", "extra", "multilib", "testing", "extra-testing", "multilib-testing", "gnome-unstable", "kde-unstable"}



class DependencyType(Enum):
    """Type of dependency."""

    RUNTIME = "depends"
    BUILD = "makedepends"
    CHECK = "checkdepends"


@dataclass
class Dependency:
    """A package dependency."""

    name: str
    version_constraint: str | None = None
    dep_type: DependencyType = DependencyType.RUNTIME
    is_aur: bool = False

    @classmethod
    def parse(cls, dep_string: str, dep_type: DependencyType = DependencyType.RUNTIME) -> "Dependency":
        """Parse dependency string like 'package>=1.0'.

        Args:
            dep_string: Dependency string
            dep_type: Type of dependency

        Returns:
            Dependency instance
        """
        # Handle version constraints
        for op in [">=", "<=", "=", ">", "<"]:
            if op in dep_string:
                name, constraint = dep_string.split(op, 1)
                return cls(name=name, version_constraint=f"{op}{constraint}", dep_type=dep_type)
        return cls(name=dep_string, dep_type=dep_type)


@dataclass
class BuildOrder:
    """Ordered list of packages to build."""

    packages: list[str] = field(default_factory=list)
    aur_dependencies: dict[str, list[Dependency]] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.packages)

    def __len__(self):
        return len(self.packages)


class DependencyResolver:
    """Resolve dependencies for AUR packages using topological sort."""

    def __init__(self, aur_client: AURClient):
        """Initialize resolver with AUR client.

        Args:
            aur_client: AURClient instance for fetching package info
        """
        self.aur_client = aur_client
        self._pacman_cache: dict[str, set[str]] = {}  # repo -> packages
        self._pacman_checked = False

    def _refresh_pacman_cache(self, sync: bool = False) -> None:
        """Refresh cache of packages available from official repos.
        
        Args:
            sync: Whether to synchronize pacman databases first using sudo pacman -Sy
        """
        try:
            if sync:
                logger.info("Synchronizing pacman databases...")
                subprocess.run(
                    ["sudo", "pacman", "-Sy", "--noconfirm"],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            result = subprocess.run(
                ["pacman", "-Sl"],
                capture_output=True,
                text=True,
                check=True,
            )
            self._pacman_cache = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    repo, name = parts[0], parts[1]
                    if repo not in self._pacman_cache:
                        self._pacman_cache[repo] = set()
                    self._pacman_cache[repo].add(name)
            
            self._pacman_checked = True
            total_pkgs = sum(len(pkgs) for pkgs in self._pacman_cache.values())
            logger.debug(f"Cached {total_pkgs} packages from {len(self._pacman_cache)} repos")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to refresh pacman cache: {e}")
            if not self._pacman_cache:
                self._pacman_cache = {}

    def is_in_repos(self, name: str, include_all: bool = True) -> str | None:
        """Check if package is available in repositories.

        Args:
            name: Package name (without version constraint)
            include_all: If True, check all enabled repos. If False, only official ones.

        Returns:
            Name of repository where package was found, or None if not found
        """
        if not self._pacman_checked:
            self._refresh_pacman_cache()

        # Strip version constraint
        base_name = name.split(">=")[0].split("<=")[0].split("=")[0].split(">")[0].split("<")[0]
        
        for repo, pkgs in self._pacman_cache.items():
            if not include_all and repo not in OFFICIAL_REPOS:
                continue
            if base_name in pkgs:
                return repo
        
        # Fallback: check provides via pacman -Sp
        try:
            # Use pacman -Sp --noconfirm to see if pacman can resolve it
            result = subprocess.run(
                ["pacman", "-Sp", "--noconfirm", base_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Successfully resolved. Find what it resolved to.
                # Output looks like: file:///var/cache/pacman/pkg/name-version...
                output = result.stdout.strip().split("\n")[-1]
                if output:
                    filename = output.split("/")[-1]
                    # The package name is before the version
                    import re
                    match = re.search(r"^(.*?)-[0-9].*", filename)
                    if match:
                        resolved_name = match.group(1)
                        # Now check which repo this resolved_name belongs to
                        # We already have resolved_name in our cache if it's in a repo
                        for repo, pkgs in self._pacman_cache.items():
                            if not include_all and repo not in OFFICIAL_REPOS:
                                continue
                            if resolved_name in pkgs:
                                return repo
        except Exception as e:
            logger.debug(f"Failed to resolve provides for {base_name}: {e}")

        return None

    def is_installed(self, name: str) -> bool:
        """Check if package is already installed.

        Args:
            name: Package name

        Returns:
            True if installed
        """
        base_name = name.split(">=")[0].split("<=")[0].split("=")[0].split(">")[0].split("<")[0]
        result = subprocess.run(
            ["pacman", "-Qi", base_name],
            capture_output=True,
        )
        return result.returncode == 0

    async def _get_all_dependencies(
        self,
        package: Package,
        visited: set[str],
        graph: dict[str, set[str]],
        packages: dict[str, Package],
    ) -> None:
        """Recursively fetch all AUR dependencies.

        Args:
            package: Package to get dependencies for
            visited: Set of already visited package names
            graph: Dependency graph (package -> dependencies)
            packages: Map of package names to Package objects
        """
        if package.name in visited:
            return

        visited.add(package.name)
        packages[package.name] = package
        graph[package.name] = set()

        # Collect all dependencies
        all_deps: list[str] = []
        all_deps.extend(package.depends)
        all_deps.extend(package.makedepends)
        all_deps.extend(package.checkdepends)

        aur_deps: list[str] = []
        for dep in all_deps:
            dep_parsed = Dependency.parse(dep)
            base_name = dep_parsed.name

            # Skip if in repos or already installed
            if self.is_in_repos(base_name):
                continue
            if self.is_installed(base_name):
                continue

            aur_deps.append(base_name)
            graph[package.name].add(base_name)

        # Fetch AUR dependencies
        if aur_deps:
            dep_packages = await self.aur_client.get_packages(aur_deps)
            for dep_pkg in dep_packages:
                await self._get_all_dependencies(dep_pkg, visited, graph, packages)

    def _topological_sort(self, graph: dict[str, set[str]]) -> list[str]:
        """Perform topological sort on dependency graph.

        Args:
            graph: Dependency graph (package -> its dependencies)

        Returns:
            List of packages in build order (dependencies first)

        Raises:
            ValueError: If circular dependency detected
        """
        # Ensure all nodes are in the graph (including leaf dependencies)
        all_nodes: set[str] = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)
        
        # Add missing nodes to graph
        for node in all_nodes:
            if node not in graph:
                graph[node] = set()

        # Kahn's algorithm
        # in_degree[X] = number of packages that X depends on (outgoing edges)
        # We want to process packages whose dependencies are all processed
        in_degree: dict[str, int] = {node: len(graph[node]) for node in graph}

        # Start with nodes that have no dependencies (leaves)
        queue = [node for node in graph if in_degree[node] == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # For each package that depends on this node, decrement its in-degree
            for pkg, deps in graph.items():
                if node in deps:
                    in_degree[pkg] -= 1
                    if in_degree[pkg] == 0:
                        queue.append(pkg)

        if len(result) != len(graph):
            # Find cycle
            remaining = set(graph.keys()) - set(result)
            raise ValueError(f"Circular dependency detected involving: {remaining}")

        # Result is already in correct order (dependencies first)
        return result

    def detect_cycles(self, graph: dict[str, set[str]]) -> list[list[str]]:
        """Detect circular dependencies in graph.

        Args:
            graph: Dependency graph

        Returns:
            List of cycles (each cycle is a list of package names)
        """
        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
                    return True

            path.pop()
            rec_stack.remove(node)
            return False

        for node in graph:
            if node not in visited:
                dfs(node)

        return cycles

    async def resolve(self, package_names: list[str], exclude_repo: str | None = None) -> BuildOrder:
        """Resolve dependencies and determine build order.

        Args:
            package_names: List of packages to resolve
            exclude_repo: Optional repository name to exclude from existence checks

        Returns:
            BuildOrder with packages in correct build order

        Raises:
            ValueError: If package not found or circular dependency
        """
        # Filter out packages already in repos or installed
        aur_package_names = []
        for name in package_names:
            repo = self.is_in_repos(name)
            if repo:
                if exclude_repo and repo == exclude_repo:
                    logger.debug(f"Package {name} found in excluded repo {repo}, treating as not in repos")
                else:
                    logger.info(f"Package {name} found in {repo}, skipping AUR lookup")
                    continue
            if self.is_installed(name):
                logger.info(f"Package {name} is already installed, skipping AUR lookup")
                continue
            aur_package_names.append(name)

        if not aur_package_names:
            return BuildOrder()

        logger.info(f"Resolving dependencies for: {', '.join(aur_package_names)}")

        # Fetch requested packages
        packages_list = await self.aur_client.get_packages(aur_package_names)
        if len(packages_list) != len(aur_package_names):
            found = {p.name for p in packages_list}
            missing = set(aur_package_names) - found
            raise ValueError(f"Packages not found in AUR: {missing}")

        # Build dependency graph
        visited: set[str] = set()
        graph: dict[str, set[str]] = {}
        packages: dict[str, Package] = {}

        for package in packages_list:
            await self._get_all_dependencies(package, visited, graph, packages)

        # Check for cycles
        cycles = self.detect_cycles(graph)
        if cycles:
            raise ValueError(f"Circular dependencies detected: {cycles}")

        # Topological sort
        build_order = self._topological_sort(graph)

        # Build AUR dependency map
        aur_deps: dict[str, list[Dependency]] = {}
        for name in build_order:
            pkg = packages.get(name)
            if pkg:
                deps: list[Dependency] = []
                for dep in pkg.depends:
                    parsed = Dependency.parse(dep, DependencyType.RUNTIME)
                    if not self.is_in_repos(parsed.name):
                        parsed.is_aur = True
                    deps.append(parsed)
                for dep in pkg.makedepends:
                    parsed = Dependency.parse(dep, DependencyType.BUILD)
                    if not self.is_in_repos(parsed.name):
                        parsed.is_aur = True
                    deps.append(parsed)
                for dep in pkg.checkdepends:
                    parsed = Dependency.parse(dep, DependencyType.CHECK)
                    if not self.is_in_repos(parsed.name):
                        parsed.is_aur = True
                    deps.append(parsed)
                aur_deps[name] = deps

        logger.info(f"Build order: {' -> '.join(build_order)}")
        return BuildOrder(packages=build_order, aur_dependencies=aur_deps)
