"""Security tests — fail-closed invariants (INV-01).

Verifies that any ambiguity about tenant/account/inbox/capability
blocks the operation with FailClosedError.
"""

from __future__ import annotations

import pytest

from iara.contracts.errors import FailClosedError
from iara.tenancy.resolver import InMemoryTenantRepository, TenantResolver


@pytest.mark.unit
@pytest.mark.security
class TestTenantResolverFailClosed:
    """Tests for fail-closed tenant resolution."""

    @pytest.mark.asyncio
    async def test_empty_tenant_key_fails_closed(self) -> None:
        """Empty tenant key must raise FailClosedError."""
        repo = InMemoryTenantRepository()
        resolver = TenantResolver(repository=repo)
        with pytest.raises(FailClosedError):
            await resolver.resolve("")

    @pytest.mark.asyncio
    async def test_unknown_tenant_key_fails_closed(self) -> None:
        """Unknown tenant key must raise FailClosedError."""
        repo = InMemoryTenantRepository()
        resolver = TenantResolver(repository=repo)
        with pytest.raises(FailClosedError):
            await resolver.resolve("nonexistent_tenant")

    @pytest.mark.asyncio
    async def test_whitespace_tenant_key_fails_closed(self) -> None:
        """Whitespace-only tenant key must raise FailClosedError."""
        repo = InMemoryTenantRepository()
        resolver = TenantResolver(repository=repo)
        with pytest.raises(FailClosedError):
            await resolver.resolve("   ")

    @pytest.mark.asyncio
    async def test_incomplete_tenant_record_fails_closed(self) -> None:
        """Incomplete tenant record (missing provider_account_id) must fail closed."""
        repo = InMemoryTenantRepository()
        repo.register(
            "incomplete_tenant",
            {
                "tenant_id": "12345678-1234-5678-1234-567812345678",
                "name": "Incomplete",
                "status": "active",
                # Missing: provider_account_id
            },
        )
        resolver = TenantResolver(repository=repo)
        with pytest.raises(FailClosedError):
            await resolver.resolve("incomplete_tenant")

    @pytest.mark.asyncio
    async def test_suspended_tenant_fails_closed(self) -> None:
        """Suspended tenant must raise FailClosedError."""
        import uuid as uuid_mod

        repo = InMemoryTenantRepository()
        repo.register(
            "suspended_tenant",
            {
                "tenant_id": str(uuid_mod.uuid4()),
                "name": "Suspended",
                "status": "suspended",
                "provider": "chatwoot",
                "provider_account_id": "acct_001",
            },
        )
        resolver = TenantResolver(repository=repo)
        with pytest.raises(FailClosedError):
            await resolver.resolve("suspended_tenant")


@pytest.mark.unit
@pytest.mark.security
class TestCapabilityFailClosed:
    """Tests for fail-closed capability resolution."""

    def test_unknown_intent_denied(
        self, chatwoot_registry: object, synthetic_tenant_id: str, synthetic_account_id: str
    ) -> None:
        """Unknown intent must be denied (not fail-opened)."""
        resolution = chatwoot_registry.resolve_intent(  # type: ignore[attr-defined]
            intent="nonexistent_capability_xyz",
            tenant_id=synthetic_tenant_id,
            account_id_ref=synthetic_account_id,
        )
        assert resolution.allowed is False
        assert resolution.resolved_tool_name is None

    def test_empty_intent_fails_closed(
        self, chatwoot_registry: object, synthetic_tenant_id: str, synthetic_account_id: str
    ) -> None:
        """Empty intent must raise FailClosedError."""
        with pytest.raises(FailClosedError):
            chatwoot_registry.resolve_intent(  # type: ignore[attr-defined]
                intent="",
                tenant_id=synthetic_tenant_id,
                account_id_ref=synthetic_account_id,
            )
