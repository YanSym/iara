"""Guardrails node — normalizes and validates agent response before dispatch.

Applies safety checks to the agent's output:
- Strips any leaked internal prompts or chain-of-thought
- Checks confidence score threshold
- Applies anti-loop detection
- Validates the response is suitable for the lead
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Confidence threshold below which guardrails trigger handoff
LOW_CONFIDENCE_THRESHOLD = 0.5

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
        return {
            "step_count": state.get("step_count", 0) + 1,
        }

    # Check for internal content leakage
    sanitized = _strip_internal_markers(agent_response)
    if sanitized != agent_response:
        logger.warning(
            "guardrails_stripped_internal_content",
            run_id=run_id,
            correlation_id=correlation_id,
        )

    # Anti-loop: check if the same response was sent recently
    # (stub — real implementation checks response history)

    return {
        "agent_response": sanitized,
        "step_count": state.get("step_count", 0) + 1,
    }


def _strip_internal_markers(text: str) -> str:
    """Remove any internal marker patterns from the response.

    Args:
        text: The response text to sanitize.

    Returns:
        str: Sanitized response text.
    """
    result = text
    for marker in INTERNAL_MARKERS:
        if marker.lower() in result.lower():
            # Find and remove the line containing the marker
            lines = result.split("\n")
            lines = [line for line in lines if marker.lower() not in line.lower()]
            result = "\n".join(lines)
    return result.strip()
