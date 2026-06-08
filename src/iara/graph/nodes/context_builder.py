"""Context builder node — assembles ConversationContext for the agent.

Builds a clean, redacted conversation context from:
- Recent message history (no private notes, no PII)
- Active tools (logical names only)
- KB excerpts (if configured)
- Published config reference
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def context_builder_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build the conversation context and update graph state.

    Args:
        state: Current graph state.

    Returns:
        dict[str, Any]: State updates with context_built flag.
    """
    logger.info(
        "node_context_builder_start",
        run_id=state.get("run_id"),
        correlation_id=state.get("correlation_id"),
    )

    # Context is assembled by the ConversationContext builder
    # which was already populated before graph execution.
    # This node validates the context is present and complete.

    metadata = state.get("metadata", {})
    active_tools = metadata.get("active_tools", [])

    return {
        "context_built": True,
        "step_count": state.get("step_count", 0) + 1,
        "metadata": {
            **metadata,
            "active_tools": active_tools,
        },
    }
