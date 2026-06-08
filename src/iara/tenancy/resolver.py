"""Tenant resolver — resolves a tenant key to a verified TenantContext.

The resolver queries Postgres for the tenant record and caches the result
for ``iara_tenant_cache_ttl_seconds``. A cache miss or stale entry triggers
a fresh database query.

Per INV-01: if the tenant cannot be resolved unambiguously, FailClosedError
is raised. No permissive fallback.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from iara.contracts.errors import FailClosedError
from iara.contracts.tenancy import TenantContext, TenantStatus
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class TenantRepository(Protocol):
    """Protocol for the tenant data access layer."""

    async def get_by_key(self, tenant_key: str) -> dict[str, Any] | None:
        """Fetch a tenant record by its public webhook key.

        Args:
            tenant_key: The public webhook key.

        Returns:
            dict | None: Tenant record dict or None if not found.
        """
        ...


class InMemoryTenantRepository:
    """In-memory tenant repository for testing and local development.

    This is a fake implementation. In production, this is replaced by
    a SQLAlchemy-backed implementation in ``persistence/repositories/``.
    """

    def __init__(self, tenants: dict[str, dict[str, Any]] | None = None) -> None:
        self._tenants: dict[str, dict[str, Any]] = tenants or {}

    async def get_by_key(self, tenant_key: str) -> dict[str, Any] | None:
        """Return the tenant record for the given key, or None.

        Args:
            tenant_key: The public webhook key.

        Returns:
            dict | None: Tenant record or None.
        """
        return self._tenants.get(tenant_key)

    def register(self, tenant_key: str, record: dict[str, Any]) -> None:
        """Register a tenant for testing.

        Args:
            tenant_key: The public webhook key.
            record: The tenant record dict.
        """
        self._tenants[tenant_key] = record


def _make_tenant_ref(tenant_key: str) -> str:
    """Create an opaque tenant reference safe for logging.

    Args:
        tenant_key: The real tenant key.

    Returns:
        str: SHA-256 short hash prefix for use in logs.
    """
    digest = hashlib.sha256(tenant_key.encode()).hexdigest()
    return f"tenant:{digest[:12]}"


class TenantResolver:
    """Resolves a tenant key to a verified, immutable TenantContext.

    Caches resolved contexts for ``cache_ttl_seconds`` to reduce DB load.
    The cache is per-resolver instance (one per worker process).

    Per INV-01: any ambiguity raises FailClosedError immediately.
    Per INV-02: the resolved TenantContext must be re-verified before each
    external side effect.

    Args:
        repository: The tenant data access implementation.
        cache_ttl_seconds: How long to cache resolved contexts.
    """

    def __init__(
        self,
        repository: TenantRepository,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._repository = repository
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[TenantContext, float]] = {}

    async def resolve(self, tenant_key: str) -> TenantContext:
        """Resolve a tenant key to a verified TenantContext.

        Args:
            tenant_key: The public webhook key from the URL path.

        Returns:
            TenantContext: Verified, immutable tenant context.

        Raises:
            FailClosedError: If the tenant key cannot be resolved unambiguously
                or the tenant is suspended/offboarded.
        """
        if not tenant_key or not tenant_key.strip():
            raise FailClosedError("tenant_key is empty — cannot resolve tenant")

        # Check cache
        import time

        now = time.monotonic()
        if tenant_key in self._cache:
            ctx, cached_at = self._cache[tenant_key]
            if now - cached_at < self._cache_ttl_seconds:
                ctx.assert_active()
                return ctx

        # Fetch from DB
        record = await self._repository.get_by_key(tenant_key)
        if record is None:
            raise FailClosedError(
                f"Tenant key {_make_tenant_ref(tenant_key)!r} not found — fail-closed"
            )

        try:
            ctx = TenantContext(
                tenant_id=UUID(str(record["tenant_id"])),
                tenant_key=tenant_key,
                tenant_name=str(record.get("name", "unknown")),
                status=TenantStatus(record.get("status", "active")),
                provider_account_id=str(record["provider_account_id"]),
                provider=str(record.get("provider", "chatwoot")),
                resolved_at=datetime.now(UTC),
            )
        except (KeyError, ValueError) as exc:
            raise FailClosedError(
                f"Tenant record for {_make_tenant_ref(tenant_key)!r} is incomplete or malformed"
            ) from exc

        ctx.assert_active()
        self._cache[tenant_key] = (ctx, now)

        logger.info(
            "tenant_resolved",
            tenant_ref=_make_tenant_ref(tenant_key),
            status=ctx.status,
            provider=ctx.provider,
        )
        return ctx

    def invalidate(self, tenant_key: str) -> None:
        """Remove a tenant from the cache, forcing a fresh DB lookup.

        Args:
            tenant_key: The tenant key to evict.
        """
        self._cache.pop(tenant_key, None)
