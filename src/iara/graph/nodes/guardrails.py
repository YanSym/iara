"""Guardrails node — normalizes and validates agent response before dispatch.

Applies safety checks to the agent's output, in order:
1. Strip leaked internal prompts / chain-of-thought markers.
2. Blocklist check — if the response contains any globally banned term,
   replace it with the neutral refusal message (never forward the original).
3. Anti-loop detection (stub — checks response history).
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger
from iara.security.content_filter import BLOCKED_RESPONSE, content_filter

logger = get_logger(__name__)

# Patterns that indicate potential prompt leakage
INTERNAL_MARKERS = [
    "<system>",
    "</system>",
    "<internal>",
    "chain-of-thought",
    "TOOL_CALL:",
    "INTERNAL:",
]


async def guardrails_node(state: dict[str, Any]) -> dict[str, Any]:
    """Apply guardrails to the agent response.

    Args:
        state: Current graph state.

    Returns:
        dict[str, Any]: State updates with sanitized agent_response.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")
    agent_response = state.get("agent_response", "")

    logger.info(
        "node_guardrails_start",
        run_id=run_id,
        correlation_id=correlation_id,
    )

    if not agent_response:
        return {"step_count": state.get("step_count", 0) + 1}

    # 1. Strip internal content leakage
    sanitized = _strip_internal_markers(agent_response)
    if sanitized != agent_response:
        logger.warning(
            "guardrails_stripped_internal_content",
            run_id=run_id,
            correlation_id=correlation_id,
        )

    # 2. Blocklist check — replace entirely if any banned term is found.
    #    The matched term is logged (redacted at log-sink level) but the
    #    original response is never forwarded downstream.
    if content_filter.contains_blocked_content(sanitized):
        matched = content_filter.first_match(sanitized)
        logger.warning(
            "guardrails_blocklist_triggered",
            run_id=run_id,
            correlation_id=correlation_id,
            matched_pattern=matched,  # redacted by log processor in prod
        )
        return {
            "agent_response": BLOCKED_RESPONSE,
            "step_count": state.get("step_count", 0) + 1,
        }

    # 3. Anti-loop: check if the same response was sent recently
    # (stub — real implementation checks response history)

    return {
        "agent_response": sanitized,
        "step_count": state.get("step_count", 0) + 1,
    }


def _strip_internal_markers(text: str) -> str:
    """Remove lines containing internal marker patterns from the response."""
    result = text
    for marker in INTERNAL_MARKERS:
        if marker.lower() in result.lower():
            lines = result.split("\n")
            lines = [line for line in lines if marker.lower() not in line.lower()]
            result = "\n".join(lines)
    return result.strip()
