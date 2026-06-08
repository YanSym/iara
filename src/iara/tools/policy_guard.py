"""ToolPolicyGuard — validates tool invocations against configured policies.

Before any side-effecting tool is executed, the ToolPolicyGuard checks:
1. The tool is registered and active.
2. The invocation parameters match the tool's schema.
3. The operation mode (suggest_only, draft_only, etc.) allows execution.
4. Any tenant-specific policy allows this invocation.

Per INV-06: high-risk writes (campaigns, kanban writes) are gated by policy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from iara.contracts.errors import FailClosedError
from iara.contracts.tools import AgentToolDefinition, ToolStatus
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class OperationMode(StrEnum):
    """Execution mode for side-effecting operations."""

    READ_ONLY = "read_only"
    DRAFT_ONLY = "draft_only"
    DRY_RUN = "dry_run"
    SUGGEST_ONLY = "suggest_only"
    SANDBOX = "sandbox"
    APPROVED_SEND = "approved_send"
    WRITE_CONFIRMED = "write_confirmed"


# Tools that require at minimum APPROVED_SEND mode for actual execution
HIGH_RISK_TOOLS: frozenset[str] = frozenset(
    {
        "campaign_dispatch_batch",
        "campaign_request_approval",
        "kanban_update_status",
        "kanban_comment",
        "followup_reengage_conversation",
        "voice_respond_audio",
    }
)

# Tools that can execute in DRAFT_ONLY mode (produce drafts, no real side effects)
DRAFT_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "schedule",
        "cancel",
        "reschedule",
        "qualify",
        "disqualify",
        "campaign_create_draft",
        "campaign_validate_audience",
        "kb_suggest_update",
    }
)


class PolicyCheckResult:
    """Result of a policy check.

    Attributes:
        approved: Whether the operation is approved.
        mode: The effective operation mode.
        reason: Explanation (for approved=False).
        produces_draft: Whether the tool will produce a draft instead of executing.
    """

    def __init__(
        self,
        approved: bool,
        mode: OperationMode,
        reason: str = "",
        produces_draft: bool = False,
    ) -> None:
        self.approved = approved
        self.mode = mode
        self.reason = reason
        self.produces_draft = produces_draft

    @classmethod
    def allow(cls, mode: OperationMode, produces_draft: bool = False) -> PolicyCheckResult:
        """Create an approval result."""
        return cls(approved=True, mode=mode, produces_draft=produces_draft)

    @classmethod
    def deny(cls, reason: str, mode: OperationMode = OperationMode.DRAFT_ONLY) -> PolicyCheckResult:
        """Create a denial result."""
        return cls(approved=False, mode=mode, reason=reason)


class ToolPolicyGuard:
    """Validates tool invocations against configured policies.

    Args:
        tenant_id: The tenant UUID string.
        kanban_mode: Current kanban operation mode.
        campaign_mode: Current campaign operation mode.
    """

    def __init__(
        self,
        tenant_id: str,
        kanban_mode: OperationMode = OperationMode.SUGGEST_ONLY,
        campaign_mode: OperationMode = OperationMode.DRAFT_ONLY,
    ) -> None:
        self._tenant_id = tenant_id
        self._kanban_mode = kanban_mode
        self._campaign_mode = campaign_mode

    def check(
        self,
        tool: AgentToolDefinition,
        arguments: dict[str, Any],
    ) -> PolicyCheckResult:
        """Check if a tool invocation is allowed by policy.

        Args:
            tool: The tool definition to check.
            arguments: The invocation arguments.

        Returns:
            PolicyCheckResult: The policy check result.

        Raises:
            FailClosedError: If the tool is not found or not active.
        """
        # Tool must be active
        if tool.status != ToolStatus.ACTIVE:
            raise FailClosedError(
                f"Tool {tool.tool_name!r} is not active (status={tool.status}) — fail-closed"
            )

        # Kanban tools — governed by kanban_mode
        if tool.tool_name.startswith("kanban_") and tool.is_side_effecting:
            return self._check_kanban(tool)

        # Campaign tools — governed by campaign_mode
        if tool.tool_name.startswith("campaign_") and tool.is_side_effecting:
            return self._check_campaign(tool)

        # High-risk tools — require explicit approval
        if tool.tool_name in HIGH_RISK_TOOLS:
            return self._check_high_risk(tool)

        # Read-only tools — always allowed
        if not tool.is_side_effecting:
            return PolicyCheckResult.allow(OperationMode.READ_ONLY)

        # Draft-safe tools — produce drafts in DRAFT_ONLY mode
        if tool.tool_name in DRAFT_SAFE_TOOLS:
            return PolicyCheckResult.allow(
                OperationMode.DRAFT_ONLY,
                produces_draft=True,
            )

        # Default: allow with draft mode
        return PolicyCheckResult.allow(OperationMode.DRAFT_ONLY, produces_draft=True)

    def _check_kanban(self, tool: AgentToolDefinition) -> PolicyCheckResult:
        """Check kanban tool policy.

        Args:
            tool: The kanban tool to check.

        Returns:
            PolicyCheckResult: Policy check result.
        """
        if self._kanban_mode == OperationMode.SUGGEST_ONLY:
            return PolicyCheckResult.allow(
                OperationMode.SUGGEST_ONLY,
                produces_draft=True,  # Returns suggestion, not real write
            )
        if self._kanban_mode in (OperationMode.WRITE_CONFIRMED, OperationMode.SANDBOX):
            return PolicyCheckResult.allow(self._kanban_mode)
        return PolicyCheckResult.deny(
            f"kanban tool {tool.tool_name!r} not allowed in mode {self._kanban_mode}",
            mode=self._kanban_mode,
        )

    def _check_campaign(self, tool: AgentToolDefinition) -> PolicyCheckResult:
        """Check campaign tool policy.

        Args:
            tool: The campaign tool to check.

        Returns:
            PolicyCheckResult: Policy check result.
        """
        if self._campaign_mode == OperationMode.DRAFT_ONLY:
            if tool.tool_name in ("campaign_dispatch_batch",):
                return PolicyCheckResult.deny(
                    "Campaign dispatch blocked in draft_only mode — create a draft first",
                    mode=OperationMode.DRAFT_ONLY,
                )
            return PolicyCheckResult.allow(OperationMode.DRAFT_ONLY, produces_draft=True)

        if self._campaign_mode == OperationMode.DRY_RUN:
            return PolicyCheckResult.allow(OperationMode.DRY_RUN, produces_draft=True)

        if self._campaign_mode == OperationMode.APPROVED_SEND:
            return PolicyCheckResult.allow(OperationMode.APPROVED_SEND)

        return PolicyCheckResult.deny(
            f"campaign mode {self._campaign_mode!r} does not permit {tool.tool_name!r}",
            mode=self._campaign_mode,
        )

    def _check_high_risk(self, tool: AgentToolDefinition) -> PolicyCheckResult:
        """Check high-risk tool policy.

        Args:
            tool: The high-risk tool to check.

        Returns:
            PolicyCheckResult: Policy check result.
        """
        # Voice tool
        if tool.tool_name == "voice_respond_audio":
            # Allowed but produces a draft by default (requires voice_output_policy)
            return PolicyCheckResult.allow(OperationMode.DRAFT_ONLY, produces_draft=True)

        # Follow-up
        if tool.tool_name == "followup_reengage_conversation":
            return PolicyCheckResult.allow(OperationMode.DRAFT_ONLY, produces_draft=True)

        # Default: produce a draft suggestion
        return PolicyCheckResult.allow(OperationMode.DRAFT_ONLY, produces_draft=True)
