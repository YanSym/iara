"""Agent Tools contracts.

Defines the request/result shapes for Agent Tool invocations. Agent Tools are
the business tools exposed to the LLM by logical name — the LLM never sees raw
MCP tool names or catalog entries.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ToolStatus(StrEnum):
    """Lifecycle status of an Agent Tool registration."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"
    SANDBOX = "sandbox"


class ToolResultStatus(StrEnum):
    """Status of a tool invocation result."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    POLICY_BLOCKED = "policy_blocked"
    PENDING_HITL = "pending_hitl"
    DRAFT_CREATED = "draft_created"


class ToolInvocationRequest(BaseModel):
    """A request to invoke an Agent Tool.

    Created when the LLM makes a tool call. Validated by ToolPolicyGuard
    before execution.

    Attributes:
        invocation_id: Unique identifier for this invocation.
        tool_name: Logical tool name (NOT a raw MCP tool name).
        arguments: Tool arguments validated against the tool's schema.
        tenant_id: Tenant UUID for cross-tenant verification.
        conversation_id: Conversation context.
        correlation_id: Distributed tracing ID.
        idempotency_key: Key for deduplication.
        call_id: LLM tool call ID (for response correlation).
    """

    invocation_id: str
    tool_name: str = Field(description="Logical Agent Tool name — never a raw MCP name")
    arguments: dict[str, Any] = Field(default_factory=dict)
    tenant_id: UUID
    conversation_id: str
    correlation_id: str
    idempotency_key: str
    call_id: str = Field(description="LLM tool call ID for response correlation")


class ToolInvocationResult(BaseModel):
    """Result of an Agent Tool invocation.

    Only sanitized result data is included. Raw backend responses, sensitive
    data, and internal error details are stripped.

    Attributes:
        invocation_id: The invocation this result belongs to.
        tool_name: Logical tool name.
        status: Execution status.
        result_summary: Sanitized, human-readable summary for the agent.
        result_data: Structured result data (sanitized, schema-validated).
        draft_ref: Reference to a created draft (for draft-producing tools).
        outbox_command_id: If a side effect was queued, the command ID.
        error_code: Sanitized error code if status is FAILED.
        error_summary: Sanitized error message (no raw backend details).
        call_id: LLM tool call ID for response correlation.
    """

    invocation_id: str
    tool_name: str
    status: ToolResultStatus
    result_summary: str = Field(description="Sanitized summary for the LLM's context")
    result_data: dict[str, Any] = Field(default_factory=dict)
    draft_ref: str | None = None
    outbox_command_id: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    call_id: str


class AgentToolDefinition(BaseModel):
    """Definition of an Agent Tool as registered in the AgentToolRegistry.

    Attributes:
        tool_name: Logical tool name.
        display_name: Human-readable name.
        description: Tool description for the LLM prompt (must not expose internal details).
        parameters_schema: JSON Schema for the tool's parameters.
        status: Current lifecycle status.
        skill_ref: Reference to the usage skill document.
        policy_ref: Reference to the tool policy.
        backend_binding_ref: Reference to the backend adapter binding.
        requires_policy_check: Whether ToolPolicyGuard must approve before execution.
        is_side_effecting: Whether this tool causes external side effects.
        default_mode: Default execution mode (e.g. suggest_only, draft_only).
    """

    tool_name: str
    display_name: str
    description: str = Field(description="Sanitized description for LLM — no internal details")
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    status: ToolStatus = ToolStatus.DRAFT
    skill_ref: str | None = None
    policy_ref: str | None = None
    backend_binding_ref: str | None = None
    requires_policy_check: bool = True
    is_side_effecting: bool = True
    default_mode: str = "draft_only"
