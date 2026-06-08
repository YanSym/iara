"""Security tests — redaction invariants (INV-05).

Verifies that sensitive data never appears in serialized output,
model_dump(), or log events.
"""

from __future__ import annotations

import pytest

from iara.contracts.tenancy import TenantContext
from iara.security.redaction import redact_dict
from tests.fixtures.synthetic_events import (
    make_payload_with_sensitive_fields,
    make_private_note_payload,
    payload_to_bytes,
)


@pytest.mark.unit
@pytest.mark.security
class TestSensitiveFieldsNeverLeaked:
    """Verifies sensitive fields never appear in normalized output."""

    def test_private_note_content_not_in_normalized_event(
        self, synthetic_tenant_context: TenantContext
    ) -> None:
        """Private note content must not appear in normalized event."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer

        payload = make_private_note_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        # Serialize to JSON
        serialized = event.model_dump_json()

        # Private note content must not appear
        assert "Private note:" not in serialized
        assert event.is_private is True
        assert event.content_text is None

    def test_sensitive_payload_fields_are_redacted(self) -> None:
        """Sensitive fields in raw payload are redacted after normalization."""
        payload = make_payload_with_sensitive_fields()
        redacted = redact_dict(payload)

        # Token field must be redacted
        assert "sk-ant-fake-token-for-testing" not in str(redacted)
        # Phone must be redacted
        assert "+5511999999999" not in str(redacted)
        # Authorization header must be redacted
        assert "Bearer fake_token" not in str(redacted)

    def test_raw_hash_not_same_as_payload(self, synthetic_tenant_context: TenantContext) -> None:
        """RawEventRef must contain hash, not the payload itself."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer
        from tests.fixtures.synthetic_events import make_incoming_message_payload

        payload = make_incoming_message_payload(content="Schedule my appointment")
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        # The raw hash should be a hex string, not the content
        assert "Schedule my appointment" not in event.raw_event_ref.raw_hash
        assert len(event.raw_event_ref.raw_hash) == 64  # SHA-256 length

    def test_account_id_not_in_normalized_event(
        self, synthetic_tenant_context: TenantContext
    ) -> None:
        """Real account ID must not appear in normalized event serialization."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer
        from tests.fixtures.synthetic_events import make_incoming_message_payload

        real_account_id = "11111"
        payload = make_incoming_message_payload(account_id=real_account_id)
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)
        serialized = event.model_dump_json()

        # Real account ID must not be in the output
        assert real_account_id not in serialized

    def test_model_dump_excludes_private_fields(
        self, synthetic_tenant_context: TenantContext
    ) -> None:
        """model_dump() must not include private fields (_raw_url, _raw_base64)."""
        from iara.eligibility.normalizer import ChatwootEventNormalizer
        from tests.fixtures.synthetic_events import make_audio_attachment_payload

        payload = make_audio_attachment_payload()
        raw_bytes = payload_to_bytes(payload)
        normalizer = ChatwootEventNormalizer(tenant_context=synthetic_tenant_context)
        event = normalizer.normalize(payload, raw_bytes)

        # Private fields (prefixed with _) should not appear in model_dump
        dumped = event.model_dump()
        if event.attachments:
            att_dump = dumped["attachments"][0]
            assert "_raw_url" not in att_dump
            assert "_raw_base64" not in att_dump
