"""ToolSkillResolver — resolves tool usage skills and backend bindings.

Each tool has an associated skill document that describes how to use it
correctly. The resolver provides the skill text for system prompt construction
and the backend binding for execution routing.
"""

from __future__ import annotations

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# ── Default skill descriptions ─────────────────────────────────────────────────
#
# These are included in the system prompt to guide tool usage.
# They must NOT contain internal system details, provider names, or API keys.

TOOL_SKILLS: dict[str, str] = {
    "availability": (
        "Use this tool BEFORE scheduling to check available time slots. "
        "Always check availability before offering or confirming an appointment."
    ),
    "schedule": (
        "Use after checking availability. Provide the exact datetime in ISO format. "
        "Confirm details with the lead before scheduling."
    ),
    "cancel": (
        "Use to cancel an existing appointment. Always confirm the cancellation with "
        "the lead. Require an appointment reference."
    ),
    "reschedule": ("Use to move an appointment. Always check availability for the new time first."),
    "qualify": (
        "Use to mark a lead as qualified. Include a brief qualification note and "
        "the appropriate label. This is a composite operation."
    ),
    "disqualify": (
        "Use to mark a lead as not meeting qualification criteria. Always provide "
        "a reason for disqualification."
    ),
    "kanban_analyze_conversation": (
        "Use to analyze where in the pipeline this conversation belongs. "
        "Returns a suggestion only — does not change anything."
    ),
    "kanban_update_status": (
        "Updates the conversation's kanban stage. Requires active kanban policy."
    ),
    "kanban_comment": (
        "Adds a private note for kanban tracking. Never sends a public message. "
        "Requires kanban write policy."
    ),
    "lead_search": (
        "Search for lead information. Returns sanitized results only. "
        "Use for context gathering, not for displaying raw lead data to contacts."
    ),
    "history_analyze_conversations": (
        "Analyze past conversations for patterns. Read-only. "
        "Results are drafts for human review."
    ),
    "followup_reengage_conversation": (
        "Send a follow-up message. Check opt-out status, quiet hours, and "
        "maximum attempt count before using."
    ),
    "campaign_create_draft": (
        "Create a campaign draft. No messages are sent. " "Always creates a draft for human review."
    ),
    "campaign_validate_audience": (
        "Validate campaign audience size and eligibility. Returns count only — "
        "never exposes the contact list."
    ),
    "campaign_request_approval": (
        "Request human approval for a campaign. Required before any campaign send."
    ),
    "campaign_dispatch_batch": (
        "Dispatch a pre-approved campaign batch. Requires approved_send policy. "
        "Sends per-recipient with idempotency."
    ),
    "campaign_status": (
        "Check the status and metrics of a campaign run. "
        "Returns counts and status only — no contact details."
    ),
    "campaign_cancel_pending": (
        "Cancel pending messages in a campaign run. " "Cannot cancel already-sent messages."
    ),
    "kb_suggest_update": (
        "Suggest a knowledge base update based on this conversation. "
        "Creates a draft for human review. Never publishes directly."
    ),
    "voice_respond_audio": (
        "Generate an audio response. Requires voice policy. "
        "Text fallback is always available if voice generation fails."
    ),
}


class ToolSkillResolver:
    """Resolves tool usage skills and backend bindings.

    Args:
        custom_skills: Optional tenant-specific skill overrides.
    """

    def __init__(self, custom_skills: dict[str, str] | None = None) -> None:
        self._skills: dict[str, str] = {**TOOL_SKILLS}
        if custom_skills:
            self._skills.update(custom_skills)

    def get_skill(self, tool_name: str) -> str | None:
        """Get the usage skill description for a tool.

        Args:
            tool_name: The logical tool name.

        Returns:
            str | None: The skill description, or None if not found.
        """
        return self._skills.get(tool_name)

    def get_skills_for_tools(self, tool_names: list[str]) -> dict[str, str]:
        """Get skill descriptions for a list of tools.

        Args:
            tool_names: List of logical tool names.

        Returns:
            dict[str, str]: Mapping of tool_name -> skill description.
        """
        return {name: skill for name, skill in self._skills.items() if name in tool_names}

    def build_tool_guidance_section(self, tool_names: list[str]) -> str:
        """Build a tools guidance section for the system prompt.

        Args:
            tool_names: Active tool names.

        Returns:
            str: Formatted tool guidance text for the system prompt.
        """
        skills = self.get_skills_for_tools(tool_names)
        if not skills:
            return ""
        lines = ["Tool usage guidance:"]
        for name, skill in skills.items():
            lines.append(f"- {name}: {skill}")
        return "\n".join(lines)
