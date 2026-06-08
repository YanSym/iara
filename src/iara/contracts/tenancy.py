"""Tenant context contracts.

``TenantContext`` is the immutable, verified tenant identity passed through every
call. It is constructed once by the ``TenantResolver`` and re-verified before any
external side effect.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    SANDBOX = "sandbox"
    OFFBOARDED = "offboarded"


class TenantContext(BaseModel):
    """Verified, immutable tenant identity for the current request.

    This object is constructed once per request by ``TenantResolver`` after
    verifying the tenant key against the database. It is threaded through
    every call and re-verified before any external side effect.

    Attributes:
        tenant_id: Internal UUID for the tenant.
        tenant_key: Public key used in webhook URLs (e.g. ``/webhooks/chatwoot/{tenant_key}``).
        tenant_name: Human-readable tenant name for logging.
        status: Current lifecycle status.
        provider_account_id: ID of the associated provider account (e.g. Chatwoot account).
        provider: Provider name (always lowercase, e.g. ``chatwoot``).
        resolved_at: Timestamp when this context was resolved (for TTL checks).
    """

    tenant_id: UUID = Field(description="Internal UUID for this tenant")
    tenant_key: str = Field(description="Public webhook URL key")
    tenant_name: str = Field(description="Human-readable name (for logging only)")
    status: TenantStatus = Field(default=TenantStatus.ACTIVE)
    provider_account_id: str = Field(description="Provider account identifier (opaque ref)")
    provider: str = Field(default="chatwoot", description="Provider/platform name")
    resolved_at: datetime = Field(description="When this context was resolved")

    model_config = {"frozen": True}

    def assert_active(self) -> None:
        """Raise FailClosedError if the tenant is not in ACTIVE or SANDBOX status.

        Raises:
            FailClosedError: If the tenant is suspended or offboarded.
        """
        from iara.contracts.errors import FailClosedError

        if self.status not in (TenantStatus.ACTIVE, TenantStatus.SANDBOX):
            raise FailClosedError(
                f"Tenant {self.tenant_key!r} is not active (status={self.status})"
            )

    def verify_provider_account(self, account_id: str) -> None:
        """Re-verify the provider account matches this tenant context.

        This must be called immediately before any external side effect.

        Args:
            account_id: The account_id from the incoming event or command.

        Raises:
            CrossTenantError: If the account_id does not match this tenant's binding.
        """
        from iara.contracts.errors import CrossTenantError

        if account_id != self.provider_account_id:
            raise CrossTenantError(
                f"Account binding mismatch for tenant {self.tenant_key!r} — "
                "refusing to execute cross-tenant operation"
            )
