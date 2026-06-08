"""Fail-closed security guards.

These guards implement the non-negotiable invariants from ``docs/INVARIANTS.md``.
They are called at critical points in the runtime to prevent cross-tenant operations,
unauthorized production access, and policy violations.

Per INV-01: any ambiguity blocks the operation.
Per INV-02: cross-tenant mismatches raise before any network call.
Per INV-07: production is blocked without explicit authorization.
"""

from __future__ import annotations

from uuid import UUID

from iara.contracts.errors import (
    CrossTenantError,
    FailClosedError,
    ProductionBlockedError,
)
from iara.contracts.tenancy import TenantContext


def assert_active_tenant(tenant_context: TenantContext) -> None:
    """Assert that the tenant is in an active or sandbox state.

    Per INV-01: suspended or offboarded tenants block all operations.

    Args:
        tenant_context: The resolved tenant context.

    Raises:
        FailClosedError: If the tenant is suspended or offboarded.
    """
    tenant_context.assert_active()


def verify_cross_tenant(
    tenant_context: TenantContext,
    event_account_id: str,
) -> None:
    """Verify that the event's account ID matches the tenant's bound account.

    This must be called immediately before any external side effect.
    Per INV-02: a mismatch raises before any network call.

    Args:
        tenant_context: The verified tenant context.
        event_account_id: The account ID extracted from the event.

    Raises:
        CrossTenantError: If the account ID does not match the tenant binding.
    """
    tenant_context.verify_provider_account(event_account_id)


def assert_production_authorized() -> None:
    """Assert that production access is explicitly authorized.

    Per INV-07: production requires IARA_PRODUCTION_AUTHORIZED=true with
    explicit Digi2B authorization.

    Raises:
        ProductionBlockedError: If production is not authorized.
    """
    from iara.config.settings import get_settings

    settings = get_settings()
    if not settings.iara_production_authorized:
        raise ProductionBlockedError()


def assert_no_cross_tenant_in_command(
    tenant_id: UUID,
    command_tenant_id: UUID,
    context: str = "command",
) -> None:
    """Assert that a command's tenant ID matches the current tenant context.

    Args:
        tenant_id: The current operation's tenant UUID.
        command_tenant_id: The command's tenant UUID.
        context: Context string for error messages.

    Raises:
        CrossTenantError: If the tenant IDs do not match.
    """
    if tenant_id != command_tenant_id:
        raise CrossTenantError(
            f"Tenant mismatch in {context}: expected {tenant_id}, got {command_tenant_id}"
        )


def assert_not_private_note(is_private: bool, context: str = "message") -> None:
    """Assert that a message is not a private note before including in agent context.

    Private notes must never enter the agent prompt, memory, or evidence.

    Args:
        is_private: Whether the message is a private note.
        context: Context string for error messages.

    Raises:
        FailClosedError: If the message is a private note.
    """
    if is_private:
        raise FailClosedError(
            f"Private note detected in {context} — refusing to include in agent context"
        )


def assert_inbox_binding(
    tenant_context: TenantContext,
    inbox_id: str,
    allowed_inboxes: list[str] | None = None,
) -> None:
    """Assert that the inbox is bound to the tenant's configuration.

    Args:
        tenant_context: The verified tenant context.
        inbox_id: The inbox ID from the event.
        allowed_inboxes: List of allowed inbox IDs. If None, any inbox is allowed
            as long as the tenant context is active.

    Raises:
        FailClosedError: If the inbox is not in the allowed list.
    """
    if allowed_inboxes is not None and inbox_id not in allowed_inboxes:
        raise FailClosedError(
            f"Inbox {inbox_id!r} is not bound for tenant {tenant_context.tenant_key!r}"
        )
