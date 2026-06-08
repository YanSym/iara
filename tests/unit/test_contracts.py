"""Unit tests for Pydantic v2 contracts.

Tests cover: incoming, outgoing, bot/system, private note, wrong account,
attachments, audio, extra fields, and sensitive-data payloads.

All fixtures are synthetic — no real data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from iara.contracts.events import (
    AttachmentType,
    EligibilityDecision,
    EligibilityReason,
    EventType,
    MessageType,
    RawEventRef,
    SenderType,
)
from iara.contracts.tenancy import TenantContext, TenantStatus
from tests.fixtures.synthetic_events import (
    make_audio_attachment_payload,
    make_incoming_message_payload,
    make_outgoing_message_payload,
    make_private_note_payload,
    payload_to_bytes,
)


@pytest.mark.unit
class TestRawEventRef:
    """Tests for RawEventRef hash-only storage."""

    def test_creates_sha256_hash(self) -> None:
        """RawEventRef must contain a SHA-256 hash of the raw bytes."""
        raw = b'{"event": "test"}'
        ref = RawEventRef.from_raw_bytes(raw, received_at="2026-01-01T00:00:00Z")
        assert len(ref.raw_hash) == 64  # SHA-256 hex = 64 chars
        assert ref.byte_count == len(raw)

    def test_different_payloads_different_hashes(self) -> None:
        """Different payloads must produce different hashes."""
        ref1 = RawEventRef.from_raw_bytes(b"payload1", received_at="2026-01-01T00:00:00Z")
        ref2 = RawEventRef.from_raw_bytes(b"payload2", received_at="2026-01-01T00:00:00Z")
        assert ref1.raw_hash != ref2.raw_hash

    def test_no_raw_payload_in_model(self) -> None:
        """RawEventRef must not contain the actual payload."""
        raw = b'{"sensitive": "data"}'
        ref = RawEventRef.from_raw_bytes(raw, received_at="2026-01-01T00:00:00Z")
        serialized = ref.model_dump_json()
        assert b"sensitive" not in serialized.encode()
        assert b"data" not in serialized.encode()


@pytest.mark.unit
class TestNormalizedChatwootEvent:
    """Tests for NormalizedChatwootEvent normalization."""

    def test_normalizes_incoming_message(self, synthetic_tenant_context: TenantContext) -> None:
        """Incoming messages should normalize correctly."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_incoming_message_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        assert event.event_type == EventType.MESSAGE_CREATED
        assert event.message_type == MessageType.INCOMING
        assert event.sender_type == SenderType.CONTACT
        assert not event.is_private
        assert event.content_text is not None

    def test_strips_private_note_content(self, synthetic_tenant_context: TenantContext) -> None:
        """Private note content must be stripped from normalized event."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_private_note_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        assert event.is_private is True
        assert event.content_text is None  # Content stripped for private notes

    def test_outgoing_message_normalizes(self, synthetic_tenant_context: TenantContext) -> None:
        """Outgoing messages should normalize (will be rejected by eligibility)."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_outgoing_message_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        assert event.message_type == MessageType.OUTGOING

    def test_audio_attachment_strips_raw_url(self, synthetic_tenant_context: TenantContext) -> None:
        """Audio attachments must not contain raw URLs or base64 data."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_audio_attachment_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        assert len(event.attachments) == 1
        att = event.attachments[0]
        assert att.attachment_type == AttachmentType.AUDIO
        assert att.attachment_ref  # Opaque ref exists
        # These fields must NOT be set from raw payload
        assert att._raw_url is None
        assert att._raw_base64 is None

    def test_extra_fields_ignored(self, synthetic_tenant_context: TenantContext) -> None:
        """Extra/unknown fields in payload should be ignored without error."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_incoming_message_payload()
        payload["unknown_field"] = "unexpected_value"
        payload["another_extra"] = {"nested": "data"}
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)  # Should not raise

        assert event is not None

    def test_account_id_is_opaque_ref(self, synthetic_tenant_context: TenantContext) -> None:
        """Real account ID must never appear in normalized event — only opaque ref."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        real_account_id = "99999"
        payload = make_incoming_message_payload(account_id=real_account_id)
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        # Real account ID must not appear in the serialized event
        serialized = event.model_dump_json()
        assert real_account_id not in serialized
        # Opaque ref should be present
        assert event.account_id_ref.startswith("acct:")


@pytest.mark.unit
class TestEligibilityDecision:
    """Tests for EligibilityDecision factory methods."""

    def test_accept_decision(self) -> None:
        """Accept decision should have eligible=True."""
        decision = EligibilityDecision.accept()
        assert decision.eligible is True
        assert decision.reason == EligibilityReason.ACCEPTED

    def test_reject_outgoing(self) -> None:
        """Reject decision should have eligible=False."""
        decision = EligibilityDecision.reject(EligibilityReason.OUTGOING_MESSAGE)
        assert decision.eligible is False
        assert decision.reason == EligibilityReason.OUTGOING_MESSAGE

    def test_reject_private_note(self) -> None:
        """Private note rejection."""
        decision = EligibilityDecision.reject(EligibilityReason.PRIVATE_NOTE)
        assert decision.eligible is False

    def test_enrichment_needed(self) -> None:
        """Enrichment decision."""
        decision = EligibilityDecision.needs_enrichment(["inbox_channel_type"])
        assert decision.eligible is False
        assert decision.enrichment_needed is True
        assert "inbox_channel_type" in decision.enrichment_fields


@pytest.mark.unit
class TestTenantContext:
    """Tests for TenantContext validation and guards."""

    def test_active_tenant_passes(self) -> None:
        """Active tenant should pass assert_active()."""
        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.ACTIVE,
            provider_account_id="acct_001",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        ctx.assert_active()  # Should not raise

    def test_sandbox_tenant_passes(self) -> None:
        """Sandbox tenant should pass assert_active()."""
        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.SANDBOX,
            provider_account_id="acct_001",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        ctx.assert_active()  # Should not raise

    def test_suspended_tenant_fails(self) -> None:
        """Suspended tenant must raise FailClosedError."""
        from iara.contracts.errors import FailClosedError

        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.SUSPENDED,
            provider_account_id="acct_001",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        with pytest.raises(FailClosedError):
            ctx.assert_active()

    def test_account_mismatch_raises_cross_tenant_error(self) -> None:
        """Account mismatch must raise CrossTenantError."""
        from iara.contracts.errors import CrossTenantError

        ctx = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_key="test",
            tenant_name="Test",
            status=TenantStatus.ACTIVE,
            provider_account_id="correct_account",
            provider="chatwoot",
            resolved_at=datetime.now(UTC),
        )
        with pytest.raises(CrossTenantError):
            ctx.verify_provider_account("wrong_account")
