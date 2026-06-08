"""Provider layer contracts.

Defines the data shapes for provider commands, capability resolution, and
mutation results. The LLM never sees these directly — they are internal
runtime contracts used by the ProviderAdapter and MCP layer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CommandStatus(StrEnum):
    """Status of a provider command in the outbox."""

    PENDING = "pending"
    SENT = "sent"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class RiskClass(StrEnum):
    """Risk classification for a provider capability."""

    READ = "read"
    LOW_WRITE = "low_write"
    HIGH_WRITE = "high_write"
    CRITICAL = "critical"


class ProviderSecurityContext(BaseModel):
    """Security context for a provider operation.

    Verified immediately before executing any provider command.

    Attributes:
        tenant_id: Tenant UUID.
        provider: Provider name.
        account_id_ref: Opaque account reference for cross-tenant check.
        inbox_id: Inbox identifier.
        capability_name: The resolved capability to execute.
        risk_class: Risk level of this operation.
    """

    tenant_id: UUID
    provider: str
    account_id_ref: str = Field(description="Opaque account ref for cross-tenant verification")
    inbox_id: str
    capability_name: str
    risk_class: RiskClass


class CapabilityResolution(BaseModel):
    """Result of resolving a runtime intent to a concrete provider capability.

    The LLM never sees this object. It is used internally by the
    ChatwootMcpRegistry + CapabilityResolver.

    Attributes:
        intent: The high-level runtime intent (e.g. ``send_message``).
        resolved_tool_name: The real MCP tool name resolved from the registry.
        allowed: Whether this capability is allowed by the allowlist/denylist.
        risk_class: Risk level of the resolved capability.
        requires_readback: Whether readback is required after execution.
        denial_reason: Reason if allowed=False.
    """

    intent: str
    resolved_tool_name: str | None = None
    allowed: bool
    risk_class: RiskClass = RiskClass.READ
    requires_readback: bool = True
    denial_reason: str | None = None

    @classmethod
    def denied(cls, intent: str, reason: str) -> CapabilityResolution:
        """Create a denied capability resolution.

        Args:
            intent: The intent that was denied.
            reason: Sanitized reason for denial.

        Returns:
            CapabilityResolution: Denied resolution.
        """
        return cls(intent=intent, allowed=False, denial_reason=reason)


class ProviderCommand(BaseModel):
    """A command queued to the provider outbox for execution.

    Commands are created during graph execution but executed asynchronously
    by the outbox drainer. They are NEVER executed directly inside a
    replayable graph node.

    Attributes:
        command_id: Unique command identifier.
        idempotency_key: Key for deduplication in the outbox.
        tenant_id: Tenant UUID.
        provider: Provider name.
        account_id_ref: Opaque account reference.
        capability_name: The resolved MCP capability to invoke.
        parameters: Validated, sanitized parameters for the capability.
        risk_class: Risk classification.
        requires_readback: Whether readback must confirm execution.
        correlation_id: For distributed tracing.
        status: Current outbox status.
        retry_count: Number of retry attempts so far.
    """

    command_id: str
    idempotency_key: str
    tenant_id: UUID
    provider: str
    account_id_ref: str
    capability_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_class: RiskClass = RiskClass.LOW_WRITE
    requires_readback: bool = True
    correlation_id: str
    status: CommandStatus = CommandStatus.PENDING
    retry_count: int = 0


class ProviderMutationResult(BaseModel):
    """Result of executing a provider command.

    Contains only sanitized metadata — no raw provider responses.

    Attributes:
        command_id: The command that was executed.
        idempotency_key: Deduplication key.
        success: Whether the command succeeded.
        readback_confirmed: Whether readback confirmed the mutation.
        result_ref: Opaque reference to the result (e.g. message ID hash).
        error_code: Sanitized error code if failed.
        error_summary: Sanitized error summary (no raw provider messages).
    """

    command_id: str
    idempotency_key: str
    success: bool
    readback_confirmed: bool = False
    result_ref: str | None = None
    error_code: str | None = None
    error_summary: str | None = None


class OperationalAction(BaseModel):
    """A structured operational action derived from agent output.

    This is an intermediate representation between agent tool calls and
    concrete ProviderCommands. The ToolPolicyGuard validates this before
    it becomes a ProviderCommand.

    Attributes:
        action_type: The type of action (``send_message``, ``assign_label``, etc.).
        logical_tool_name: The Agent Tool name that generated this action.
        parameters: Action parameters (validated by tool schema).
        policy_checked: Whether the ToolPolicyGuard has validated this.
        approved: Whether the action is approved to proceed.
        approval_reason: Reason if approved=False.
    """

    action_type: str
    logical_tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    policy_checked: bool = False
    approved: bool = False
    approval_reason: str | None = None
