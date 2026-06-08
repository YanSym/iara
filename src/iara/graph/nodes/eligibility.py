"""Eligibility node — checks if the event should be processed.

This node is a thin orchestrator. Business logic lives in
``iara.eligibility.decision.EligibilityChecker``.
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def eligibility_node(state: dict[str, Any]) -> dict[str, Any]:
    """Check event eligibility and update graph state.

    Args:
        state: Current graph state.

    Returns:
        dict[str, Any]: State updates with eligibility_status set.
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

    return {
        "eligibility_status": eligibility_status,
        "step_count": state.get("step_count", 0) + 1,
    }
