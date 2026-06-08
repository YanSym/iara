"""AgentToolRegistry — catalog of available Agent Tools.

The registry is the single source of truth for what tools are available to
the agent. Only ACTIVE, published tools appear in the agent's context.

Per INV-03: the agent only sees logical tool names — never raw MCP tool names.
"""

from __future__ import annotations

from iara.contracts.tools import AgentToolDefinition, ToolStatus
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class AgentToolRegistry:
    """Registry of Agent Tools available to the conversational agent.

    Tools must be explicitly registered and in ACTIVE status to appear
    in the agent's tool list. Inactive, draft, or deprecated tools are
    invisible to the agent.

    Args:
        tenant_id: The tenant this registry is for.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._tools: dict[str, AgentToolDefinition] = {}

    def register(self, tool: AgentToolDefinition) -> None:
        """Register a tool in the registry.

        Args:
            tool: The tool definition to register.
        """
        self._tools[tool.tool_name] = tool
        logger.debug("tool_registered", tool_name=tool.tool_name, status=tool.status)

    def get_active_tools(self) -> list[AgentToolDefinition]:
        """Return all tools currently in ACTIVE status.

        These are the tools that will be presented to the agent.

        Returns:
            list[AgentToolDefinition]: Active tool definitions.
        """
        return [t for t in self._tools.values() if t.status == ToolStatus.ACTIVE]

    def get_tool(self, tool_name: str) -> AgentToolDefinition | None:
        """Get a tool by name, regardless of status.

        Args:
            tool_name: The logical tool name.

        Returns:
            AgentToolDefinition | None: The tool definition, or None if not found.
        """
        return self._tools.get(tool_name)

    def is_active(self, tool_name: str) -> bool:
        """Check if a tool is active.

        Args:
            tool_name: The logical tool name.

        Returns:
            bool: True if the tool is registered and active.
        """
        tool = self._tools.get(tool_name)
        return tool is not None and tool.status == ToolStatus.ACTIVE

    def get_tool_names_for_prompt(self) -> list[str]:
        """Return active tool names formatted for the agent prompt.

        Returns:
            list[str]: Active logical tool names.
        """
        return [t.tool_name for t in self.get_active_tools()]

    @classmethod
    def build_default(cls, tenant_id: str) -> AgentToolRegistry:
        """Build a registry with all default tools in ACTIVE status.

        Args:
            tenant_id: The tenant UUID string.

        Returns:
            AgentToolRegistry: Registry populated with default tools.
        """
        registry = cls(tenant_id=tenant_id)
        for tool in _DEFAULT_TOOLS:
            registry.register(tool)
        return registry


# ── Default Agent Tool definitions ───────────────────────────────────────────
#
# These tools are the business capabilities exposed to the agent.
# Each has a schema, description, and policy reference.
# The agent sees ONLY these logical names — never raw MCP tool names.

_DEFAULT_TOOLS: list[AgentToolDefinition] = [
    AgentToolDefinition(
        tool_name="availability",
        display_name="Check Availability",
        description="Check available appointment slots. Call this before scheduling.",
        parameters_schema={
            "type": "object",
            "properties": {
                "date_range_start": {"type": "string", "description": "ISO date start"},
                "date_range_end": {"type": "string", "description": "ISO date end"},
                "service_type": {"type": "string"},
            },
            "required": ["date_range_start"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="read_only",
    ),
    AgentToolDefinition(
        tool_name="schedule",
        display_name="Schedule Appointment",
        description="Schedule an appointment. Always check availability first.",
        parameters_schema={
            "type": "object",
            "properties": {
                "datetime_iso": {"type": "string"},
                "service_type": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["datetime_iso"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="cancel",
        display_name="Cancel Appointment",
        description="Cancel an existing appointment.",
        parameters_schema={
            "type": "object",
            "properties": {
                "appointment_ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["appointment_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="reschedule",
        display_name="Reschedule Appointment",
        description="Reschedule an existing appointment to a new time.",
        parameters_schema={
            "type": "object",
            "properties": {
                "appointment_ref": {"type": "string"},
                "new_datetime_iso": {"type": "string"},
            },
            "required": ["appointment_ref", "new_datetime_iso"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="qualify",
        display_name="Qualify Lead",
        description="Mark a lead as qualified with a label, private note, and optional notification.",  # noqa: E501
        parameters_schema={
            "type": "object",
            "properties": {
                "qualification_note": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": [],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="disqualify",
        display_name="Disqualify Lead",
        description="Mark a lead as disqualified with a reason.",
        parameters_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": ["reason"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="kanban_analyze_conversation",
        display_name="Analyze Conversation Kanban Stage",
        description="Analyze the current conversation to suggest a kanban stage.",
        parameters_schema={
            "type": "object",
            "properties": {
                "include_history": {"type": "boolean", "default": False},
            },
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="suggest_only",
    ),
    AgentToolDefinition(
        tool_name="kanban_update_status",
        display_name="Update Kanban Status",
        description="Update the conversation's kanban stage (requires policy).",
        parameters_schema={
            "type": "object",
            "properties": {
                "stage": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["stage"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="suggest_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="kanban_comment",
        display_name="Add Kanban Comment",
        description="Add a private note to the conversation for kanban tracking.",
        parameters_schema={
            "type": "object",
            "properties": {
                "comment": {"type": "string"},
            },
            "required": ["comment"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="suggest_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="lead_search",
        display_name="Search Lead",
        description="Search for lead information (read-only).",
        parameters_schema={
            "type": "object",
            "properties": {
                "search_terms": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["search_terms"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="read_only",
    ),
    AgentToolDefinition(
        tool_name="history_analyze_conversations",
        display_name="Analyze Conversation History",
        description="Analyze historical conversations (read-only, produces drafts).",
        parameters_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "maximum": 50},
                "focus": {"type": "string"},
            },
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="read_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="followup_reengage_conversation",
        display_name="Follow Up on Conversation",
        description="Send a follow-up message to re-engage a conversation.",
        parameters_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["message"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="campaign_create_draft",
        display_name="Create Campaign Draft",
        description="Create a campaign message draft (draft_only — no messages sent).",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_name": {"type": "string"},
                "message_template": {"type": "string"},
                "target_description": {"type": "string"},
            },
            "required": ["campaign_name", "message_template"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="campaign_validate_audience",
        display_name="Validate Campaign Audience",
        description="Validate a campaign audience (count only — no contact list exposed).",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_draft_ref": {"type": "string"},
            },
            "required": ["campaign_draft_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="read_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="campaign_request_approval",
        display_name="Request Campaign Approval",
        description="Request human approval to send a campaign.",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_draft_ref": {"type": "string"},
                "approver_ref": {"type": "string"},
            },
            "required": ["campaign_draft_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="campaign_dispatch_batch",
        display_name="Dispatch Campaign Batch",
        description="Send an approved campaign batch (requires approved_send policy).",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_run_ref": {"type": "string"},
                "batch_size": {"type": "integer", "default": 10},
            },
            "required": ["campaign_run_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="campaign_status",
        display_name="Campaign Status",
        description="Check the status of a campaign run.",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_run_ref": {"type": "string"},
            },
            "required": ["campaign_run_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="read_only",
    ),
    AgentToolDefinition(
        tool_name="campaign_cancel_pending",
        display_name="Cancel Pending Campaign",
        description="Cancel pending messages in a campaign run.",
        parameters_schema={
            "type": "object",
            "properties": {
                "campaign_run_ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["campaign_run_ref"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
    AgentToolDefinition(
        tool_name="kb_suggest_update",
        display_name="Suggest KB Update",
        description="Suggest an update to the knowledge base (creates draft — never publishes directly).",  # noqa: E501
        parameters_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "suggested_content": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["topic", "suggested_content"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=False,
        default_mode="draft_only",
    ),
    AgentToolDefinition(
        tool_name="voice_respond_audio",
        display_name="Respond with Audio",
        description="Generate an audio response (requires voice_output_policy; fallback to text).",
        parameters_schema={
            "type": "object",
            "properties": {
                "text_content": {"type": "string"},
                "voice_ref": {"type": "string"},
            },
            "required": ["text_content"],
        },
        status=ToolStatus.ACTIVE,
        is_side_effecting=True,
        default_mode="draft_only",
        requires_policy_check=True,
    ),
]
