"""ChatwootMcpRegistry — explicit registry of Chatwoot MCP capabilities.

This registry is the ONLY source of truth for what Chatwoot MCP capabilities
are available and allowed. The LLM never sees this registry or raw tool names.

Per INV-03: the LLM never receives the raw Chatwoot MCP catalog.
Per INV-01: if a capability cannot be resolved unambiguously, it is denied.
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
# These represent the well-known Chatwoot MCP tools. In production, this is
# loaded from the database (tenant_mcp_servers / tenant_mcp_server_tools).
# For local development and tests, this default registry is used.

DEFAULT_CHATWOOT_CAPABILITIES: list[McpCapabilityEntry] = [
    # Read-only capabilities
    McpCapabilityEntry(
        intent="get_account_info",
        mcp_tool_name="chatwoot_get_account_context",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_messages",
        mcp_tool_name="chatwoot_list_messages",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="list_labels",
        mcp_tool_name="chatwoot_list_labels",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="read_conversation",
        mcp_tool_name="chatwoot_get_conversation",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    McpCapabilityEntry(
        intent="get_contact",
        mcp_tool_name="chatwoot_get_contact",
        risk_class=RiskClass.READ,
        requires_readback=False,
    ),
    # Low-risk write capabilities
    McpCapabilityEntry(
        intent="send_message",
        mcp_tool_name="chatwoot_send_message",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    McpCapabilityEntry(
        intent="add_label",
        mcp_tool_name="chatwoot_add_label",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    McpCapabilityEntry(
        intent="add_private_note",
        mcp_tool_name="chatwoot_add_private_note",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    McpCapabilityEntry(
        intent="assign_conversation",
        mcp_tool_name="chatwoot_assign_conversation",
        risk_class=RiskClass.LOW_WRITE,
        requires_readback=True,
    ),
    # High-risk write capabilities
    McpCapabilityEntry(
        intent="close_conversation",
        mcp_tool_name="chatwoot_update_conversation_status",
        risk_class=RiskClass.HIGH_WRITE,
        requires_readback=True,
    ),
    McpCapabilityEntry(
        intent="update_contact",
        mcp_tool_name="chatwoot_update_contact",
        risk_class=RiskClass.HIGH_WRITE,
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

        # Build intent → capability index
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
        # Empty or blank intent is always fail-closed
        if not intent or not intent.strip():
            raise FailClosedError("Intent must not be empty or blank")

        # Cross-tenant guard — fail-closed
        if tenant_id != self._tenant_id:
            raise FailClosedError(
                "Registry tenant mismatch — cannot resolve intent for different tenant"
            )
        if account_id_ref != self._account_id_ref:
            raise FailClosedError(
                "Registry account mismatch — cannot resolve intent for different account"
            )

        # Global denylist check
        if intent in self._global_denylist:
            return CapabilityResolution.denied(intent, f"intent {intent!r} is globally denied")

        # Look up the capability
        cap = self._by_intent.get(intent)
        if cap is None:
            return CapabilityResolution.denied(intent, f"intent {intent!r} is not registered")

        # Status check
        if cap.status == McpCapabilityStatus.INACTIVE:
            return CapabilityResolution.denied(intent, f"capability for {intent!r} is inactive")

        # Account-level denylist
        if account_id_ref in cap.denied_accounts:
            return CapabilityResolution.denied(
                intent, f"capability for {intent!r} is denied for this account"
            )

        # Account-level allowlist (if non-empty, must be in the set)
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
