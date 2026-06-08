"""Typed exception hierarchy for the IAra runtime.

All exceptions extend ``IaraError``. Provider errors are mapped through
``ProviderErrorMapper`` before propagating to ensure sanitized messages.

Example::

    raise FailClosedError("tenant_id ambiguous — cannot resolve inbox binding")
"""

from __future__ import annotations


class IaraError(Exception):
    """Base class for all IAra runtime exceptions.

    Args:
        message: A sanitized, non-sensitive error message.
        code: Optional machine-readable error code.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class FailClosedError(IaraError):
    """Raised when any ambiguity about tenant/account/inbox/capability is detected.

    Per INV-01: any such ambiguity MUST block the operation. No permissive fallback.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="FAIL_CLOSED")


class CrossTenantError(IaraError):
    """Raised when a cross-tenant data access or operation is attempted.

    Per INV-02: tenant context must be re-verified before every external side effect.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CROSS_TENANT")


class PolicyViolationError(IaraError):
    """Raised when an operation is blocked by a configured policy.

    Per INV-06: high-risk writes require explicit policy + HITL.
    """

    def __init__(self, message: str, policy_name: str | None = None) -> None:
        self.policy_name = policy_name
        super().__init__(message, code="POLICY_VIOLATION")


class ReadbackFailedError(IaraError):
    """Raised when readback confirmation of a side effect fails.

    Per INV-04: side effects must be confirmed via readback before marking complete.
    """

    def __init__(self, message: str, command_ref: str | None = None) -> None:
        self.command_ref = command_ref
        super().__init__(message, code="READBACK_FAILED")


class ProviderError(IaraError):
    """Raised for errors originating from an external provider (e.g. Chatwoot).

    Messages are sanitized through ``ProviderErrorMapper`` before this is raised.
    """

    def __init__(
        self, message: str, provider: str | None = None, status_code: int | None = None
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(message, code="PROVIDER_ERROR")


class IdempotencyError(IaraError):
    """Raised when an idempotency key conflict is detected.

    This indicates the operation was already executed and should not be retried.
    """

    def __init__(self, message: str, idempotency_key: str | None = None) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(message, code="IDEMPOTENCY_CONFLICT")


class LeaseConflictError(IaraError):
    """Raised when a conversation lease cannot be acquired (another worker holds it)."""

    def __init__(self, message: str, conversation_id: str | None = None) -> None:
        self.conversation_id = conversation_id
        super().__init__(message, code="LEASE_CONFLICT")


class EligibilityError(IaraError):
    """Raised when an event fails eligibility checks and must be rejected."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        self.reason = reason
        super().__init__(message, code="ELIGIBILITY_REJECTED")


class MediaProcessingError(IaraError):
    """Raised when media processing fails and no fallback is available."""

    def __init__(self, message: str, media_type: str | None = None) -> None:
        self.media_type = media_type
        super().__init__(message, code="MEDIA_PROCESSING_ERROR")


class ToolExecutionError(IaraError):
    """Raised when a tool execution fails after policy validation."""

    def __init__(self, message: str, tool_name: str | None = None) -> None:
        self.tool_name = tool_name
        super().__init__(message, code="TOOL_EXECUTION_ERROR")


class ConfigPublishError(IaraError):
    """Raised when a configuration publication fails validation or transaction."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIG_PUBLISH_ERROR")


class ProductionBlockedError(IaraError):
    """Raised when a production path is attempted without explicit authorization.

    Per INV-07: production requires IARA_PRODUCTION_AUTHORIZED=true.
    """

    def __init__(self) -> None:
        super().__init__(
            "Production access blocked. Set IARA_PRODUCTION_AUTHORIZED=true"
            " with explicit Digi2B authorization.",
            code="PRODUCTION_BLOCKED",
        )
