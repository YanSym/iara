"""Security module — redaction, fail-closed guards, and PII protection."""

from iara.security.guards import (
    assert_active_tenant,
    assert_production_authorized,
    verify_cross_tenant,
)
from iara.security.redaction import (
    RedactionProcessor,
    redact_dict,
    redact_string,
)

__all__ = [
    "RedactionProcessor",
    "redact_dict",
    "redact_string",
    "assert_active_tenant",
    "assert_production_authorized",
    "verify_cross_tenant",
]
