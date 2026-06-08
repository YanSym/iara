"""Capability resolver — maps runtime intents to concrete provider capabilities.

The LLM never sees raw MCP tool names. The CapabilityResolver maps high-level
runtime intents (e.g. ``send_message``, ``assign_label``) to concrete MCP
capability names via the ChatwootMcpRegistry.

Per INV-01: if the capability cannot be resolved unambiguously, the operation
is blocked with a fail-closed error.
Per INV-03: the LLM never sees raw provider MCP tool names.
"""

from __future__ import annotations

from iara.contracts.errors import FailClosedError
from iara.contracts.provider import CapabilityResolution, RiskClass
from iara.observability.logging import get_logger

logger = get_logger(__name__)


# ── Known intents and their default risk classification ───────────────────────

INTENT_RISK_MAP: dict[str, RiskClass] = {
    # Read-only intents
    "read_conversation": RiskClass.READ,
    "list_messages": RiskClass.READ,
    "get_account_info": RiskClass.READ,
    "list_labels": RiskClass.READ,
    "get_contact": RiskClass.READ,
    # Low-risk writes
    "send_message": RiskClass.LOW_WRITE,
    "add_label": RiskClass.LOW_WRITE,
    "remove_label": RiskClass.LOW_WRITE,
    "add_private_note": RiskClass.LOW_WRITE,
    "assign_conversation": RiskClass.LOW_WRITE,
    # High-risk writes
    "close_conversation": RiskClass.HIGH_WRITE,
    "assign_team": RiskClass.HIGH_WRITE,
    "update_contact": RiskClass.HIGH_WRITE,
    # Critical operations
    "bulk_action": RiskClass.CRITICAL,
    "delete_contact": RiskClass.CRITICAL,
}


class CapabilityResolver:
    """Resolves runtime intents to concrete provider capabilities.

    Uses the ``ChatwootMcpRegistry`` as the source of truth for what capabilities
    are available and allowed for a given tenant/account.

    Args:
        registry: The ChatwootMcpRegistry for this tenant.
    """

    def __init__(self, registry: ChatwootMcpRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        intent: str,
        tenant_id: str,
        account_id_ref: str,
    ) -> CapabilityResolution:
        """Resolve an intent to a concrete, allowed capability.

        Args:
            intent: High-level runtime intent (e.g. ``send_message``).
            tenant_id: Tenant UUID string.
            account_id_ref: Opaque account reference.

        Returns:
            CapabilityResolution: Resolution result (may be denied).

        Raises:
            FailClosedError: If the intent is unknown or the capability
                cannot be resolved unambiguously.
        """
        if not intent or not intent.strip():
            raise FailClosedError("intent is empty — cannot resolve capability")

        # Look up the registry mapping
        resolution = self._registry.resolve_intent(
            intent=intent,
            tenant_id=tenant_id,
            account_id_ref=account_id_ref,
        )

        if not resolution.allowed:
            logger.warning(
                "capability_denied",
                intent=intent,
                reason=resolution.denial_reason,
                tenant_ref=tenant_id[:8],
            )

        return resolution


# Import here to avoid circular imports
from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry  # noqa: E402
