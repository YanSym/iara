"""Security tests — cross-tenant protection (INV-02).

Verifies that any account binding mismatch raises CrossTenantError
before any external call is made.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from iara.contracts.errors import CrossTenantError, FailClosedError
from iara.contracts.tenancy import TenantContext, TenantStatus
from iara.security.guards import assert_no_cross_tenant_in_command, verify_cross_tenant


@pytest.mark.unit
@pytest.mark.security
class TestCrossTenantGuards:
    """Tests for cross-tenant protection invariants."""

    def test_matching_account_passes(self) -> None:
        """Matching account ID must not raise."""
        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.ACTIVE,
            provider_account_id="account_001",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        verify_cross_tenant(ctx, "account_001")  # Should not raise

    def test_mismatched_account_raises_cross_tenant_error(self) -> None:
        """Mismatched account ID must raise CrossTenantError."""
        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.ACTIVE,
            provider_account_id="account_001",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        with pytest.raises(CrossTenantError):
            verify_cross_tenant(ctx, "different_account_999")

    def test_tenant_id_mismatch_raises(self) -> None:
        """Tenant UUID mismatch must raise CrossTenantError."""
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        assert tenant_a != tenant_b
        with pytest.raises(CrossTenantError):
            assert_no_cross_tenant_in_command(tenant_a, tenant_b)

    def test_tenant_id_match_passes(self) -> None:
        """Matching tenant IDs must not raise."""
        tenant_id = uuid.uuid4()
        assert_no_cross_tenant_in_command(tenant_id, tenant_id)  # Should not raise


@pytest.mark.unit
@pytest.mark.security
class TestRegistryCrossTenantProtection:
    """Tests for ChatwootMcpRegistry cross-tenant protection."""

    def test_registry_rejects_different_tenant(self, chatwoot_registry: object) -> None:
        """Registry must raise FailClosedError for different tenant ID."""

        with pytest.raises(FailClosedError):
            chatwoot_registry.resolve_intent(  # type: ignore[attr-defined]
                intent="send_message",
                tenant_id="different_tenant_id",
                account_id_ref="some_account",
            )

    def test_registry_rejects_different_account(
        self, chatwoot_registry: object, synthetic_tenant_id: str
    ) -> None:
        """Registry must raise FailClosedError for different account ref."""

        with pytest.raises(FailClosedError):
            chatwoot_registry.resolve_intent(  # type: ignore[attr-defined]
                intent="send_message",
                tenant_id=synthetic_tenant_id,
                account_id_ref="different_account_ref",
            )


@pytest.mark.unit
@pytest.mark.security
class TestFakeAdapterCrossTenantProtection:
    """Tests for FakeChatwootAdapter cross-tenant protection."""

    @pytest.mark.asyncio
    async def test_adapter_rejects_tenant_mismatch(
        self,
        fake_chatwoot_adapter: object,
        synthetic_tenant_id: str,
        synthetic_account_id: str,
    ) -> None:
        """Fake adapter must raise CrossTenantError for tenant mismatch."""
        import uuid as uuid_mod

        from iara.contracts.provider import (
            ProviderCommand,
            ProviderSecurityContext,
            RiskClass,
        )

        command = ProviderCommand(
            command_id=str(uuid_mod.uuid4()),
            idempotency_key="idem_001",
            tenant_id=uuid_mod.UUID(synthetic_tenant_id),
            provider="chatwoot",
            account_id_ref=synthetic_account_id,
            capability_name="send_message",
            parameters={"conversation_id": "conv_001", "content": "test"},
            correlation_id="corr_001",
        )
        # Different tenant in security context
        security_ctx = ProviderSecurityContext(
            tenant_id=uuid_mod.uuid4(),  # Different tenant!
            provider="chatwoot",
            account_id_ref=synthetic_account_id,
            inbox_id="inbox_001",
            capability_name="send_message",
            risk_class=RiskClass.LOW_WRITE,
        )

        with pytest.raises(CrossTenantError):
            await fake_chatwoot_adapter.execute_command(command, security_ctx)  # type: ignore[attr-defined]
