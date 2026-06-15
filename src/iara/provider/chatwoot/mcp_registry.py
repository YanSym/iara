"""ChatwootMcpRegistry — explicit registry of Chatwoot MCP capabilities.

This registry is the ONLY source of truth for what Chatwoot MCP capabilities
are available and allowed. The LLM never sees this registry or raw tool names.

Per INV-03: the LLM never receives the raw Chatwoot MCP catalog.
Per INV-01: if a capability cannot be resolved unambiguously, it is denied.

Tool names are the real names as exposed by the Digi2B customised Chatwoot MCP
server (validated 2026-06-15 against mcp-suporte and oral-unic-cuiaba, 132 tools
each). The intent names match the capability_name values placed in ProviderCommands
by graph nodes and tool catalog builders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from iara.contracts.errors import FailClosedError
from iara.contracts.provider import CapabilityResolution, RiskClass
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class McpCapabilityStatus(StrEnum):
    """Lifecycle status of a registered MCP capability."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SANDBOX = "sandbox"


@dataclass
class McpCapabilityEntry:
    """A registered Chatwoot MCP capability.

    Attributes:
        intent: High-level runtime intent this capability implements.
            Must match the capability_name placed in ProviderCommands.
        mcp_tool_name: The real MCP tool name (never exposed to the LLM).
        risk_class: Risk classification.
        requires_readback: Whether readback is needed after execution.
        status: Current lifecycle status.
        allowed_accounts: Set of account_id_refs that may use this. Empty = all.
        denied_accounts: Set of account_id_refs explicitly blocked.
    """

    intent: str
    mcp_tool_name: str
    risk_class: RiskClass
    requires_readback: bool = True
    status: McpCapabilityStatus = McpCapabilityStatus.ACTIVE
    allowed_accounts: frozenset[str] = field(default_factory=frozenset)
    denied_accounts: frozenset[str] = field(default_factory=frozenset)


# ── Default Chatwoot MCP capability registry ──────────────────────────────────
#
# Real tool names come from the Digi2B customised Chatwoot MCP (2026-06-15).
# The intent names match what command_dispatch_node and ToolExecutor emit as
# capability_name in ProviderCommands.
#
# MCP endpoint pattern:  https://app.digi2b.com/mcp/<account_id>/<slug>
# Auth header:           Api-Access-Token: <token>
# Transport:             HTTP (JSON-RPC 2.0, method "tools/call")

DEFAULT_CHATWOOT_CAPABILITIES: list[McpCapabilityEntry] = [
    # ── Account / context (read-only) ──────────────────────────────────────────
    McpCapabilityEntry(
        intent="get_account_info",
        mcp_tool_name="account_context",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    # ── Conversation reads ──────────────────────────────────────────────────────
    McpCapabilityEntry(
        intent="read_conversation",
        mcp_tool_name="conversations_get",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_messages",
        mcp_tool_name="messages_list",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_labels",
        mcp_tool_name="conversations_get_labels",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    # ── Contact reads ───────────────────────────────────────────────────────────
    McpCapabilityEntry(
        intent="get_contact",
        mcp_tool_name="contacts_get",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    # ── Message sends (LOW_WRITE) ───────────────────────────────────────────────
    # intent "send_message" — used by command_dispatch_node for the agent reply
    McpCapabilityEntry(
        intent="send_message",
        mcp_tool_name="conversation_message_send",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # intent "followup_reengage_conversation" — side-effect of the followup tool
    McpCapabilityEntry(
        intent="followup_reengage_conversation",
        mcp_tool_name="conversation_message_send",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # intent "kanban_comment" — private note on a conversation
    McpCapabilityEntry(
        intent="kanban_comment",
        mcp_tool_name="conversation_message_send",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # ── Labels (LOW_WRITE) — set_labels REPLACES the full list (read-first) ─────
    # intent "label_conversation" — used by qualify / disqualify tools
    McpCapabilityEntry(
        intent="label_conversation",
        mcp_tool_name="conversations_set_labels",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # ── Conversation operations (LOW_WRITE) ─────────────────────────────────────
    McpCapabilityEntry(
        intent="assign_conversation",
        mcp_tool_name="conversation_assignments_assign",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # ── Conversation status (HIGH_WRITE) ────────────────────────────────────────
    McpCapabilityEntry(
        intent="close_conversation",
        mcp_tool_name="conversations_toggle_status",
        risk_class=RiskClass.HIGH_WRITE,
        requires_readback=True,
    ),
    # ── Contact mutation (HIGH_WRITE) ───────────────────────────────────────────
    McpCapabilityEntry(
        intent="update_contact",
        mcp_tool_name="contacts_update",
        risk_class=RiskClass.HIGH_WRITE,
        requires_readback=True,
    ),
    # ── Kanban reads (intent ≠ mcp_tool_name per INV-03) ────────────────────────
    McpCapabilityEntry(
        intent="list_kanban_boards",
        mcp_tool_name="kanban_boards_list",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_kanban_steps",
        mcp_tool_name="kanban_steps_list",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_kanban_tasks",
        mcp_tool_name="kanban_tasks_list",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    # ── Kanban writes (LOW_WRITE) ────────────────────────────────────────────────
    # intent "kanban_update_status" — used when kanban mode is write_confirmed
    McpCapabilityEntry(
        intent="kanban_update_status",
        mcp_tool_name="kanban_tasks_move",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    McpCapabilityEntry(
        intent="create_kanban_task",
        mcp_tool_name="kanban_tasks_create",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
]


class ChatwootMcpRegistry:
    """Explicit registry of Chatwoot MCP capabilities per tenant/account.

    This registry maps high-level runtime intents to real MCP tool names,
    with per-account allowlist/denylist enforcement.

    The LLM never sees this registry. It is used exclusively by the runtime
    to resolve capabilities before executing provider commands.

    Args:
        tenant_id: The tenant this registry is for.
        account_id_ref: Opaque account reference for binding.
        capabilities: List of registered capabilities. Defaults to built-in list.
        global_denylist: Intents that are globally denied for this registry.
    """

    def __init__(
        self,
        tenant_id: str,
        account_id_ref: str,
        capabilities: list[McpCapabilityEntry] | None = None,
        global_denylist: frozenset[str] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._account_id_ref = account_id_ref
        self._global_denylist: frozenset[str] = global_denylist or frozenset()

        cap_list = capabilities if capabilities is not None else DEFAULT_CHATWOOT_CAPABILITIES
        self._by_intent: dict[str, McpCapabilityEntry] = {cap.intent: cap for cap in cap_list}

    def resolve_intent(
        self,
        intent: str,
        tenant_id: str,
        account_id_ref: str,
    ) -> CapabilityResolution:
        """Resolve an intent to a concrete MCP capability.

        Args:
            intent: The high-level runtime intent.
            tenant_id: Tenant UUID string (must match registry tenant).
            account_id_ref: Opaque account reference (must match binding).

        Returns:
            CapabilityResolution: Resolved capability or denial.

        Raises:
            FailClosedError: If the tenant or account does not match the registry.
        """
        if not intent or not intent.strip():
            raise FailClosedError("Intent must not be empty or blank")

        if tenant_id != self._tenant_id:
            raise FailClosedError(
                "Registry tenant mismatch — cannot resolve intent for different tenant"
            )
        if account_id_ref != self._account_id_ref:
            raise FailClosedError(
                "Registry account mismatch — cannot resolve intent for different account"
            )

        if intent in self._global_denylist:
            return CapabilityResolution.denied(intent, f"intent {intent!r} is globally denied")

        cap = self._by_intent.get(intent)
        if cap is None:
            return CapabilityResolution.denied(intent, f"intent {intent!r} is not registered")

        if cap.status == McpCapabilityStatus.INACTIVE:
            return CapabilityResolution.denied(intent, f"capability for {intent!r} is inactive")

        if account_id_ref in cap.denied_accounts:
            return CapabilityResolution.denied(
                intent, f"capability for {intent!r} is denied for this account"
            )

        if cap.allowed_accounts and account_id_ref not in cap.allowed_accounts:
            return CapabilityResolution.denied(
                intent, f"capability for {intent!r} is not allowed for this account"
            )

        return CapabilityResolution(
            intent=intent,
            resolved_tool_name=cap.mcp_tool_name,
            allowed=True,
            risk_class=cap.risk_class,
            requires_readback=cap.requires_readback,
        )

    def list_active_intents(self) -> list[str]:
        """Return a list of active (allowed) intent names.

        Returns:
            list[str]: Active intent names (NOT MCP tool names).
        """
        return [
            intent
            for intent, cap in self._by_intent.items()
            if cap.status == McpCapabilityStatus.ACTIVE and intent not in self._global_denylist
        ]
