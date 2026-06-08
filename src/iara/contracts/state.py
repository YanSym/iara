"""Graph state and context contracts.

These models define the data flowing through LangGraph nodes. All fields containing
sensitive content (raw messages, phone numbers, internal prompts) must be redacted
before any serialization to external systems.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SecurityContext(BaseModel):
    """Security context attached to every operation.

    Attributes:
        tenant_id: Verified tenant UUID.
        provider_account_ref: Opaque provider account reference.
        inbox_id: Verified inbox identifier.
        source_channel: Verified source channel type.
        correlation_id: Distributed tracing correlation ID.
        flow_version: Version of the LangGraph flow being executed.
        config_version: Version of the published agent config.
        prompt_version: Version of the prompt being used.
        tool_policy_version: Version of the tool policy being enforced.
    """

    tenant_id: UUID
    provider_account_ref: str = Field(description="Opaque account ref — not real account ID")
    inbox_id: str
    source_channel: str
    correlation_id: str
    flow_version: str = Field(default="1.0.0")
    config_version: str | None = None
    prompt_version: str | None = None
    tool_policy_version: str | None = None

    model_config = {"frozen": True}


class MediaStatus(StrEnum):
    """Processing status for a media attachment."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class MediaContext(BaseModel):
    """Result of the MediaUnderstanding subgraph.

    Raw bytes, base64, temporary URLs, and audio files are NEVER stored here.
    Only sanitized, processed representations are kept.

    Attributes:
        attachment_ref: Reference to the original attachment.
        media_type: MIME type of the original attachment.
        status: Processing status.
        extracted_text: Sanitized extracted text (transcript, OCR output, document text).
        description: Optional visual description for images.
        fallback_reason: Why fallback was triggered (if status != COMPLETE).
    """

    attachment_ref: str
    media_type: str
    status: MediaStatus = MediaStatus.PENDING
    extracted_text: str | None = None
    description: str | None = None
    fallback_reason: str | None = None


class ConversationContext(BaseModel):
    """Assembled context passed to the agent for response generation.

    Only sanitized, policy-approved content enters this context. Private notes,
    raw payloads, and internal prompt details are strictly excluded.

    Attributes:
        conversation_id: Conversation identifier (opaque).
        recent_messages: Operational history (sanitized text, no private notes).
        media_contexts: Results from MediaUnderstanding for each attachment.
        active_tools: List of logical tool names the agent may call.
        kb_excerpts: Knowledge base excerpts (read-only, sanitized).
        config_ref: Reference to the active published config.
        prompt_ref: Reference to the active prompt version.
        memory_items: Governed memory items (if memory is enabled).
        context_hash: Hash of the assembled context for audit trail.
    """

    conversation_id: str
    recent_messages: list[dict[str, str]] = Field(
        default_factory=list,
        description="Sanitized message history [{role, content}] — no private notes",
    )
    media_contexts: list[MediaContext] = Field(default_factory=list)
    active_tools: list[str] = Field(
        default_factory=list,
        description="Logical tool names visible to the agent — NOT raw MCP tool names",
    )
    kb_excerpts: list[str] = Field(default_factory=list)
    config_ref: str | None = None
    prompt_ref: str | None = None
    memory_items: list[dict[str, str]] = Field(default_factory=list)
    context_hash: str | None = None


class AgentInput(BaseModel):
    """Input to the main agent node.

    Attributes:
        security_context: Verified security context.
        conversation_context: Assembled conversation context.
        current_message: The current message text to respond to.
        run_id: LangGraph run identifier.
    """

    security_context: SecurityContext
    conversation_context: ConversationContext
    current_message: str = Field(description="Sanitized current message text")
    run_id: str = Field(description="LangGraph run identifier")


class ToolCallRequest(BaseModel):
    """A tool call requested by the agent.

    Attributes:
        tool_name: Logical tool name (NOT a raw MCP tool name).
        arguments: Tool call arguments (schema-validated).
        call_id: Unique identifier for this tool call.
    """

    tool_name: str = Field(description="Logical Agent Tool name — never a raw MCP tool name")
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str


class AgentOutput(BaseModel):
    """Output from the main agent node.

    Attributes:
        response_text: The generated response text (post-guardrails).
        tool_calls: List of tool calls requested by the agent.
        requires_hitl: Whether a HITL interrupt is needed.
        hitl_reason: Reason for HITL if required.
        confidence_score: Agent confidence (0.0–1.0); low scores trigger guardrails.
        run_id: LangGraph run identifier.
    """

    response_text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    requires_hitl: bool = False
    hitl_reason: str | None = None
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    run_id: str


class ConversationState(BaseModel):
    """LangGraph conversation state — the mutable state passed between graph nodes.

    This is the state object for the main conversational graph. Each field is
    updated by nodes and persisted via the LangGraph checkpointer.

    Attributes:
        run_id: LangGraph run identifier.
        security_context: Verified security context for this run.
        normalized_event: The normalized event triggering this run.
        eligibility_decision: Decision from the eligibility node.
        conversation_context: Assembled context for the agent.
        media_contexts: Results from the MediaUnderstanding subgraph.
        agent_input: Input prepared for the agent node.
        agent_output: Output from the agent node.
        pending_tool_calls: Tool calls yet to be executed.
        completed_tool_calls: Tool calls that have been executed.
        provider_commands: Commands queued to the outbox.
        response_sent: Whether a response has been dispatched.
        error: Sanitized error if any node failed.
        step_count: Number of graph steps completed.
    """

    run_id: str
    security_context: SecurityContext | None = None
    normalized_event: Any | None = None  # NormalizedChatwootEvent
    eligibility_decision: Any | None = None  # EligibilityDecision
    conversation_context: ConversationContext | None = None
    media_contexts: list[MediaContext] = Field(default_factory=list)
    agent_input: AgentInput | None = None
    agent_output: AgentOutput | None = None
    pending_tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    completed_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    provider_commands: list[dict[str, Any]] = Field(default_factory=list)
    response_sent: bool = False
    error: str | None = None
    step_count: int = 0
