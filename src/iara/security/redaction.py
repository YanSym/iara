"""Redaction utilities for PII, secrets, and sensitive data.

These utilities ensure that sensitive data never appears in logs, audit events,
or evidence records. They are applied at the structlog processor level and
at model serialization boundaries.

Per INVARIANT INV-05: logs, audit events, and evidence contain ONLY hashes,
refs, counts, statuses, and sanitized error messages.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# ── Sensitive field names (redacted regardless of value) ─────────────────────

SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # Credentials & tokens
        "token",
        "api_key",
        "api_token",
        "secret",
        "password",
        "credential",
        "auth_token",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer",
        "x_auth_token",
        "authorization",
        "x_api_key",
        # Raw content
        "raw_body",
        "raw_payload",
        "raw_content",
        "pin_data",
        "pinData",
        "base64",
        "raw_base64",
        "_raw_base64",
        "_raw_url",
        # PII
        "phone",
        "phone_number",
        "raw_phone",
        "contact_phone",
        "email",
        "cpf",
        "cnpj",
        "rg",
        # Provider-internal IDs (real ones)
        "account_id",
        "real_account_id",
        # Headers
        "headers",
        "cookie",
        "cookies",
        "x_forwarded_for",
        # Private notes
        "_private_content",
        "private_note",
        "private_content",
        "private_message",
        # Attachment raw data
        "attachment_url",
        "download_url",
        "temp_url",
        "temporary_url",
        "media_url",
        "file_url",
        "audio_url",
        "image_url",
        "raw_attachment",
    }
)

# ── Regex patterns for value-based redaction ─────────────────────────────────

SENSITIVE_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # Anthropic API keys
    re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,}", re.IGNORECASE),
    # Generic bearer tokens
    re.compile(r"Bearer\s+[a-zA-Z0-9\-_\.]{20,}", re.IGNORECASE),
    # Brazilian phone numbers
    re.compile(r"\+?55\s*\(?[1-9]{2}\)?\s*9?\d{4}[\s\-]?\d{4}"),
    # Brazilian CPF
    re.compile(r"\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[\-\s]?\d{2}"),
    # HTTP URLs with tokens (e.g. S3 pre-signed URLs)
    re.compile(r"https?://[^\s]+\?[^\s]*(?:token|key|signature|auth)[^\s]*", re.IGNORECASE),
    # Base64 blobs (>100 chars)
    re.compile(r"[A-Za-z0-9+/]{100,}={0,2}"),
]

REDACTED_PLACEHOLDER = "[REDACTED]"
REDACTED_HASH_PREFIX = "[SHA256:"


def _is_sensitive_field(key: str) -> bool:
    """Return True if the field name indicates sensitive content.

    Args:
        key: Field name to check.

    Returns:
        bool: True if the field should be redacted.
    """
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return normalized in SENSITIVE_FIELD_NAMES


def _contains_sensitive_pattern(value: str) -> bool:
    """Return True if the string value matches a sensitive pattern.

    Args:
        value: String value to check.

    Returns:
        bool: True if the value contains a sensitive pattern.
    """
    return any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS)


def hash_for_ref(value: str | bytes) -> str:
    """Create a safe hash reference from a sensitive value.

    Args:
        value: The sensitive value to hash.

    Returns:
        str: A ``[SHA256:<first-12-chars>...]`` reference string.
    """
    if isinstance(value, str):
        value = value.encode()
    digest = hashlib.sha256(value).hexdigest()
    return f"{REDACTED_HASH_PREFIX}{digest[:12]}...]"


def redact_string(value: str) -> str:
    """Redact sensitive patterns in a string value.

    Replaces sensitive pattern matches with ``[REDACTED]``.

    Args:
        value: The string to redact.

    Returns:
        str: The redacted string.
    """
    result = value
    for pattern in SENSITIVE_VALUE_PATTERNS:
        result = pattern.sub(REDACTED_PLACEHOLDER, result)
    return result


def redact_dict(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Recursively redact sensitive fields from a dictionary.

    Redacts fields by name (based on ``SENSITIVE_FIELD_NAMES``) and by
    value pattern (based on ``SENSITIVE_VALUE_PATTERNS``).

    Args:
        data: The dictionary to redact.
        depth: Current recursion depth (max 10 to prevent infinite recursion).

    Returns:
        dict[str, Any]: A new dictionary with sensitive fields redacted.
    """
    if depth > 10:
        return {"[REDACTED_DEEP]": True}

    result: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_field(str(key)):
            result[key] = REDACTED_PLACEHOLDER
        elif isinstance(value, dict):
            result[key] = redact_dict(value, depth + 1)
        elif isinstance(value, list):
            result[key] = _redact_list(value, depth + 1)
        elif isinstance(value, str) and _contains_sensitive_pattern(value):
            result[key] = redact_string(value)
        else:
            result[key] = value
    return result


def _redact_list(data: list[Any], depth: int) -> list[Any]:
    """Recursively redact sensitive data in a list.

    Args:
        data: The list to redact.
        depth: Current recursion depth.

    Returns:
        list[Any]: Redacted list.
    """
    result: list[Any] = []
    for item in data:
        if isinstance(item, dict):
            result.append(redact_dict(item, depth + 1))
        elif isinstance(item, list):
            result.append(_redact_list(item, depth + 1))
        elif isinstance(item, str) and _contains_sensitive_pattern(item):
            result.append(redact_string(item))
        else:
            result.append(item)
    return result


class RedactionProcessor:
    """structlog processor that redacts sensitive fields before logging.

    Attach this to the structlog processor chain to ensure sensitive data
    never appears in log output.

    Example::

        structlog.configure(
            processors=[
                RedactionProcessor(),
                structlog.processors.JSONRenderer(),
            ]
        )
    """

    def __call__(
        self,
        logger: Any,
        method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Redact sensitive fields from the structlog event dict.

        Args:
            logger: The logger instance.
            method: The log method name.
            event_dict: The event dictionary to process.

        Returns:
            dict[str, Any]: The redacted event dictionary.
        """
        return redact_dict(event_dict)
