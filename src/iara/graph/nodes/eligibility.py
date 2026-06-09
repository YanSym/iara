"""Eligibility node — checks if the event should be processed.

This node is a thin orchestrator. Business logic lives in
``iara.eligibility.decision.EligibilityChecker``.

It also detects admin commands (messages starting with /iara, /admin, @iara
sent by authorized senders) and sets the ``is_admin_command`` routing flag.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from iara.observability.logging import get_logger

if TYPE_CHECKING:
    from iara.security.command_auth import CommandAuthorizationGuard

logger = get_logger(__name__)


async def eligibility_node(
    state: dict[str, Any],
    auth_guard: CommandAuthorizationGuard | None = None,
) -> dict[str, Any]:
    """Check event eligibility and detect admin commands.

    Args:
        state: Current graph state.
        auth_guard: Optional CommandAuthorizationGuard for admin command detection.

    Returns:
        dict[str, Any]: State updates with eligibility_status and is_admin_command set.
    """
    logger.info(
        "node_eligibility_start",
        run_id=state.get("run_id"),
        correlation_id=state.get("correlation_id"),
    )

    # The eligibility check was already performed in the webhook handler
    # before the job was queued. Jobs arrive with status "pending"; since
    # ineligible events are rejected at the webhook level, treat pending as accepted.
    eligibility_status = state.get("eligibility_status", "accepted")
    if eligibility_status == "pending":
        eligibility_status = "accepted"

    is_admin_command = False
    if eligibility_status == "accepted" and auth_guard is not None:
        metadata = state.get("metadata", {})
        sender_type = metadata.get("sender_type", "contact")
        sender_ref = str(metadata.get("sender_ref", ""))

        # Find last user message for admin prefix check
        last_msg = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_msg = msg.get("content", "")
                break

        result = auth_guard.check(
            tenant_id=state.get("tenant_id", ""),
            sender_type=sender_type,
            sender_ref=sender_ref,
            message_content=last_msg,
        )

        if result.is_admin_command and not result.allowed:
            # Deny the command — treated as ineligible
            eligibility_status = "rejected_unauthorized_admin"
        elif result.is_admin_command and result.allowed:
            is_admin_command = True

    return {
        "eligibility_status": eligibility_status,
        "is_admin_command": is_admin_command,
        "step_count": state.get("step_count", 0) + 1,
    }


def build_eligibility_node(auth_guard: CommandAuthorizationGuard | None) -> Any:
    """Build eligibility_node with injected CommandAuthorizationGuard.

    Args:
        auth_guard: Authorization guard (None → admin command detection disabled).

    Returns:
        Callable: Node function ready for LangGraph.
    """
    return partial(eligibility_node, auth_guard=auth_guard)
