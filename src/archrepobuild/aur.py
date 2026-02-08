"""Async AUR RPC API client with caching and retry logic."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import aiohttp

from archrepobuild.logging import get_logger

logger = get_logger("aur")

AUR_RPC_URL = "https://aur.archlinux.org/rpc"
AUR_PACKAGE_URL = "https://aur.archlinux.org/packages"
AUR_GIT_URL = "https://aur.archlinux.org"


class PackageType(Enum):
    """Package type from AUR."""

    NORMAL = "normal"
    SPLIT = "split"


@dataclass
class Package:
    """AUR package metadata."""

    name: str
    version: str
    description: str
    url: str | None
    maintainer: str | None
    votes: int
    popularity: float
    out_of_date: datetime | None
    first_submitted: datetime
    last_modified: datetime
    depends: list[str] = field(default_factory=list)
    makedepends: list[str] = field(default_factory=list)
    checkdepends: list[str] = field(default_factory=list)
    optdepends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    license: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def git_url(self) -> str:
        """Get the git clone URL for this package."""
        return f"{AUR_GIT_URL}/{self.name}.git"

    @property
    def aur_url(self) -> str:
        """Get the AUR web page URL for this package."""
        return f"{AUR_PACKAGE_URL}/{self.name}"

    @classmethod
    def from_rpc(cls, data: dict[str, Any]) -> "Package":
        """Create Package from AUR RPC response data.

        Args:
            data: Package data from AUR RPC API

        Returns:
            Package instance
        """
        return cls(
            name=data["Name"],
            version=data["Version"],
            description=data.get("Description", ""),
            url=data.get("URL"),
            maintainer=data.get("Maintainer"),
            votes=data.get("NumVotes", 0),
            popularity=data.get("Popularity", 0.0),
            out_of_date=(
                datetime.fromtimestamp(data["OutOfDate"]) if data.get("OutOfDate") else None
            ),
            first_submitted=datetime.fromtimestamp(data["FirstSubmitted"]),
            last_modified=datetime.fromtimestamp(data["LastModified"]),
            depends=data.get("Depends", []),
            makedepends=data.get("MakeDepends", []),
            checkdepends=data.get("CheckDepends", []),
            optdepends=data.get("OptDepends", []),
            provides=data.get("Provides", []),
            conflicts=data.get("Conflicts", []),
            replaces=data.get("Replaces", []),
            license=data.get("License", []),
            keywords=data.get("Keywords", []),
        )


@dataclass
class CacheEntry:
    """Cache entry with TTL."""

    data: Package
    expires: datetime


class AURClient:
    """Async client for AUR RPC API with caching and retry."""

    def __init__(
        self,
        cache_ttl: int = 300,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        batch_size: int = 100,
    ):
        """Initialize AUR client.

        Args:
            cache_ttl: Cache time-to-live in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Base delay between retries (exponential backoff)
            batch_size: Maximum packages per batch request
        """
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.batch_size = batch_size
        self._cache: dict[str, CacheEntry] = {}
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "AURClient":
        """Async context manager entry."""
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_cached(self, name: str) -> Package | None:
        """Get package from cache if not expired.

        Args:
            name: Package name

        Returns:
            Cached package or None if not cached/expired
        """
        entry = self._cache.get(name)
        if entry and entry.expires > datetime.now():
            return entry.data
        return None

    def _set_cached(self, package: Package) -> None:
        """Store package in cache.

        Args:
            package: Package to cache
        """
        self._cache[package.name] = CacheEntry(
            data=package,
            expires=datetime.now() + timedelta(seconds=self.cache_ttl),
        )

    async def _request(
        self,
        params: list[tuple[str, Any]] | dict[str, Any],
    ) -> dict[str, Any]:
        """Make request to AUR RPC API with retry logic.

        Args:
            params: Query parameters (as list of tuples for repeated keys, or dict)

        Returns:
            JSON response data

        Raises:
            aiohttp.ClientError: If request fails after all retries
        """
        if not self._session:
            raise RuntimeError("AURClient must be used as async context manager")

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                async with self._session.get(AUR_RPC_URL, params=params) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if data.get("type") == "error":
                        raise ValueError(f"AUR API error: {data.get('error')}")
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"AUR request failed (attempt {attempt + 1}/{self.max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)

        raise last_error or RuntimeError("Request failed")

    async def get_package(self, name: str) -> Package | None:
        """Get a single package by name.

        Args:
            name: Package name

        Returns:
            Package if found, None otherwise
        """
        # Check cache first
        cached = self._get_cached(name)
        if cached:
            logger.debug(f"Cache hit for package: {name}")
            return cached

        packages = await self.get_packages([name])
        return packages[0] if packages else None

    async def get_packages(self, names: list[str]) -> list[Package]:
        """Get multiple packages by name using batch queries.

        Args:
            names: List of package names

        Returns:
            List of found packages (may be fewer than requested)
        """
        # Separate cached and uncached packages
        result: list[Package] = []
        uncached: list[str] = []

        for name in names:
            cached = self._get_cached(name)
            if cached:
                result.append(cached)
            else:
                uncached.append(name)

        if not uncached:
            return result

        # Batch request uncached packages
        for i in range(0, len(uncached), self.batch_size):
            batch = uncached[i : i + self.batch_size]
            
            # Build params as list of tuples for repeated arg[] keys
            params: list[tuple[str, Any]] = [("v", 5), ("type", "info")]
            for name in batch:
                params.append(("arg[]", name))

            data = await self._request(params)

            for pkg_data in data.get("results", []):
                package = Package.from_rpc(pkg_data)
                self._set_cached(package)
                result.append(package)

        return result

    async def search(self, query: str, by: str = "name-desc") -> list[Package]:
        """Search AUR packages.

        Args:
            query: Search query string
            by: Search field (name, name-desc, maintainer, depends, makedepends, optdepends, checkdepends)

        Returns:
            List of matching packages
        """
        params = {"v": 5, "type": "search", "by": by, "arg": query}
        data = await self._request(params)

        packages = []
        for pkg_data in data.get("results", []):
            package = Package.from_rpc(pkg_data)
            self._set_cached(package)
            packages.append(package)

        return packages

    async def is_available(self, name: str) -> bool:
        """Check if a package exists in the AUR.

        Args:
            name: Package name

        Returns:
            True if package exists
        """
        package = await self.get_package(name)
        return package is not None

    def clear_cache(self) -> None:
        """Clear the package cache."""
        self._cache.clear()
