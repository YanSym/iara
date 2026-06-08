"""Agent node — invokes the LLM to generate a response or tool calls.

The agent node is a thin orchestrator. It calls the LLM with:
- The assembled ConversationContext (no raw provider data)
- Active logical tool names (never raw MCP tool names)
- The current message

Per INV-03: the LLM never sees raw MCP tool names or the Chatwoot catalog.
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def agent_node(state: dict[str, Any], llm: Any = None) -> dict[str, Any]:
    """Invoke the LLM and update graph state with the response.

    Args:
        state: Current graph state.
        llm: The LLM client (injected; optional in tests).

    Returns:
        dict[str, Any]: State updates with agent_response and tool_calls.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")

    logger.info(
        "node_agent_start",
        run_id=run_id,
        correlation_id=correlation_id,
    )

    messages = state.get("messages", [])

    if llm is None:
        # Test stub — return a canned response
        return {
            "agent_response": "Hello! How can I help you today?",
            "tool_calls_pending": [],
            "step_count": state.get("step_count", 0) + 1,
        }

    try:
        # Invoke the LLM with the assembled context
        response = await llm.ainvoke(messages)

        # Extract text response and tool calls
        agent_response = None
        tool_calls_pending = []

        if hasattr(response, "content"):
            agent_response = response.content
        if hasattr(response, "tool_calls") and response.tool_calls:
            for tc in response.tool_calls:
                tool_calls_pending.append(
                    {
                        "call_id": tc.get("id", ""),
                        "tool_name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                    }
                )

        logger.info(
            "node_agent_complete",
            run_id=run_id,
            correlation_id=correlation_id,
            has_tool_calls=len(tool_calls_pending) > 0,
        )

        return {
            "agent_response": agent_response,
            "tool_calls_pending": tool_calls_pending,
            "step_count": state.get("step_count", 0) + 1,
        }

    except Exception as exc:
        logger.error(
            "node_agent_failed",
            run_id=run_id,
            correlation_id=correlation_id,
            error_code=type(exc).__name__,
        )
        return {
            "error": f"Agent execution failed: {type(exc).__name__}",
            "step_count": state.get("step_count", 0) + 1,
        }
