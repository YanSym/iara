"""Unit tests for redaction utilities.

Verifies that sensitive fields never appear in serialized output or logs.
"""

from __future__ import annotations

import pytest

from iara.security.redaction import (
    REDACTED_PLACEHOLDER,
    hash_for_ref,
    redact_dict,
    redact_string,
)


@pytest.mark.unit
@pytest.mark.security
class TestRedactDict:
    """Tests for dict redaction."""

    def test_redacts_token_field(self) -> None:
        """Token field must be redacted."""
        data = {"token": "sk-ant-secret-token-123", "message": "hello"}
        result = redact_dict(data)
        assert result["token"] == REDACTED_PLACEHOLDER
        assert result["message"] == "hello"

    def test_redacts_api_key_field(self) -> None:
        """api_key field must be redacted."""
        data = {"api_key": "my-secret-api-key"}
        result = redact_dict(data)
        assert result["api_key"] == REDACTED_PLACEHOLDER

    def test_redacts_phone_field(self) -> None:
        """Phone field must be redacted."""
        data = {"phone": "+5511999999999", "name": "Test User"}
        result = redact_dict(data)
        assert result["phone"] == REDACTED_PLACEHOLDER
        assert result["name"] == "Test User"  # Non-sensitive field preserved

    def test_redacts_nested_sensitive_fields(self) -> None:
        """Nested sensitive fields must be redacted."""
        data = {
            "sender": {
                "name": "Test",
                "phone": "+5511999999999",
                "type": "contact",
            }
        }
        result = redact_dict(data)
        assert result["sender"]["phone"] == REDACTED_PLACEHOLDER
        assert result["sender"]["name"] == "Test"
        assert result["sender"]["type"] == "contact"

    def test_redacts_headers(self) -> None:
        """Headers field must be redacted."""
        data = {"headers": {"Authorization": "Bearer token123", "Content-Type": "application/json"}}
        result = redact_dict(data)
        assert result["headers"] == REDACTED_PLACEHOLDER

    def test_redacts_anthropic_api_key_in_value(self) -> None:
        """API key pattern in string value must be redacted."""
        data = {"message": "key=sk-ant-api03-longfakekeyvaluehere12345678"}
        result = redact_dict(data)
        assert "sk-ant" not in result["message"]

    def test_preserves_non_sensitive_fields(self) -> None:
        """Non-sensitive fields must be preserved."""
        data = {
            "status": "ok",
            "count": 42,
            "items": ["a", "b", "c"],
            "metadata": {"version": "1.0", "env": "sandbox"},
        }
        result = redact_dict(data)
        assert result["status"] == "ok"
        assert result["count"] == 42
        assert result["items"] == ["a", "b", "c"]
        assert result["metadata"]["version"] == "1.0"

    def test_redacts_base64_blob_in_value(self) -> None:
        """Large base64 blobs in values must be redacted."""
        fake_base64 = "A" * 150  # Exceeds the 100-char threshold
        data = {"attachment_data": fake_base64}
        result = redact_dict(data)
        assert fake_base64 not in str(result["attachment_data"])

    def test_handles_empty_dict(self) -> None:
        """Empty dict should return empty dict."""
        assert redact_dict({}) == {}

    def test_handles_deep_nesting_without_infinite_loop(self) -> None:
        """Deep nesting should not cause infinite recursion."""
        data: dict = {}
        current = data
        for _i in range(15):  # 15 levels deep
            current["nested"] = {}
            current = current["nested"]

        result = redact_dict(data)  # Should not raise
        assert result is not None


@pytest.mark.unit
@pytest.mark.security
class TestRedactString:
    """Tests for string redaction."""

    def test_redacts_anthropic_key_pattern(self) -> None:
        """Anthropic API key pattern must be redacted from strings."""
        text = "Authorization: Bearer sk-ant-api03-fakekey12345678901234567890"
        result = redact_string(text)
        assert "sk-ant" not in result
        assert REDACTED_PLACEHOLDER in result

    def test_redacts_brazilian_phone(self) -> None:
        """Brazilian phone number must be redacted."""
        text = "Contact: +55 11 99999-9999"
        result = redact_string(text)
        assert "+55 11 99999-9999" not in result

    def test_preserves_safe_text(self) -> None:
        """Safe text must not be modified."""
        text = "Hello, how can I help you today?"
        result = redact_string(text)
        assert result == text


@pytest.mark.unit
@pytest.mark.security
class TestHashForRef:
    """Tests for hash_for_ref."""

    def test_creates_deterministic_hash(self) -> None:
        """Same input must always produce same hash."""
        ref1 = hash_for_ref("test_value")
        ref2 = hash_for_ref("test_value")
        assert ref1 == ref2

    def test_different_inputs_different_hashes(self) -> None:
        """Different inputs must produce different hashes."""
        ref1 = hash_for_ref("value_a")
        ref2 = hash_for_ref("value_b")
        assert ref1 != ref2

    def test_original_value_not_in_hash(self) -> None:
        """The original value must not appear in the hash output."""
        sensitive = "my-sensitive-token-value"
        ref = hash_for_ref(sensitive)
        assert sensitive not in ref
