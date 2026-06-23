"""Postgres-backed tenant repository for production environments.

Queries the ``tenants`` table by ``tenant_key`` and returns the tenant record
as a dict compatible with ``TenantResolver``.

Per INV-01: if the tenant is not found or is inactive the method returns None
and the resolver raises FailClosedError — no permissive fallback.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iara.observability.logging import get_logger
from iara.persistence.models import Tenant

logger = get_logger(__name__)


class PostgresTenantRepository:
    """Reads tenant records from Postgres by webhook key.

    Args:
        session: An active async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(self, tenant_key: str) -> dict[str, Any] | None:
        """Return the tenant record for the given webhook key, or None.

        Returns None (not raises) when the tenant is not found or not active;
        the caller (TenantResolver) raises FailClosedError.

        Args:
            tenant_key: The public webhook key from the URL path.

        Returns:
            dict | None: Tenant record dict or None.
        """
        stmt = select(Tenant).where(
            Tenant.tenant_key == tenant_key,
            Tenant.status == "active",
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "tenant_id": str(row.id),
            "name": row.name,
            "status": row.status,
            "provider": row.provider,
            "provider_account_id": row.provider_account_id,
        }
