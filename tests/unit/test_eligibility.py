"""Unit tests for the eligibility checker.

Verifies that all eligibility rules correctly accept or reject events.
"""

from __future__ import annotations

import pytest

from iara.contracts.events import EligibilityReason
from iara.contracts.tenancy import TenantContext
from iara.eligibility.decision import EligibilityChecker
from iara.eligibility.normalizer import ChatwootEventNormalizer
from tests.fixtures.synthetic_events import (
    make_bot_message_payload,
    make_incoming_message_payload,
    make_outgoing_message_payload,
    make_private_note_payload,
    payload_to_bytes,
)


def normalize(payload: dict, tenant: TenantContext):  # type: ignore[no-untyped-def]
    """Helper to normalize a payload."""
    normalizer = ChatwootEventNormalizer(tenant_context=tenant)
    return normalizer.normalize(payload, payload_to_bytes(payload))


@pytest.mark.unit
class TestEligibilityChecker:
    """Tests for EligibilityChecker."""

    @pytest.mark.asyncio
    async def test_accepts_incoming_message(self, synthetic_tenant_context: TenantContext) -> None:
        """Incoming contact messages must be accepted."""
        payload = make_incoming_message_payload()
        event = normalize(payload, synthetic_tenant_context)
        checker = EligibilityChecker(tenant_context=synthetic_tenant_context)
        decision = await checker.check(event)
        assert decision.eligible is True
        assert decision.reason == EligibilityReason.ACCEPTED

    @pytest.mark.asyncio
    async def test_rejects_outgoing_message(self, synthetic_tenant_context: TenantContext) -> None:
        """Outgoing messages must be rejected."""
        payload = make_outgoing_message_payload()
        event = normalize(payload, synthetic_tenant_context)
        checker = EligibilityChecker(tenant_context=synthetic_tenant_context)
        decision = await checker.check(event)
        assert decision.eligible is False
        assert decision.reason == EligibilityReason.OUTGOING_MESSAGE

    @pytest.mark.asyncio
    async def test_rejects_bot_sender(self, synthetic_tenant_context: TenantContext) -> None:
        """Bot-sent messages must be rejected."""
        payload = make_bot_message_payload()
        event = normalize(payload, synthetic_tenant_context)
        checker = EligibilityChecker(tenant_context=synthetic_tenant_context)
        decision = await checker.check(event)
        assert decision.eligible is False
        assert decision.reason == EligibilityReason.BOT_SENDER

    @pytest.mark.asyncio
    async def test_rejects_private_note(self, synthetic_tenant_context: TenantContext) -> None:
        """Private notes must be rejected."""
        payload = make_private_note_payload()
        event = normalize(payload, synthetic_tenant_context)
        checker = EligibilityChecker(tenant_context=synthetic_tenant_context)
        decision = await checker.check(event)
        assert decision.eligible is False
        assert decision.reason == EligibilityReason.PRIVATE_NOTE
