"""Tool executor node — processes pending tool calls from the agent.

This node is a thin orchestrator. It delegates to AgentToolMcpGateway
for each pending tool call and accumulates results.

Per INV-04: side-effecting tools emit commands to the outbox, never
execute provider calls directly within this replayable node.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def tool_executor_node(
    state: dict[str, Any],
    gateway: Any = None,
) -> dict[str, Any]:
    """Execute pending tool calls and update graph state.

    Args:
        state: Current graph state.
        gateway: AgentToolMcpGateway (injected; optional in tests).

    Returns:
        dict[str, Any]: State updates with tool_results.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")
    pending = state.get("tool_calls_pending", [])

    if not pending:
        return {"step_count": state.get("step_count", 0) + 1}

    logger.info(
        "node_tool_executor_start",
        run_id=run_id,
        correlation_id=correlation_id,
        pending_count=len(pending),
    )

    if gateway is None:
        # Test stub — return mock results
        results = [
            {
                "call_id": tc.get("call_id", ""),
                "tool_name": tc.get("tool_name", ""),
                "status": "success",
                "result_summary": f"Tool {tc.get('tool_name', '')} executed (stub)",
            }
            for tc in pending
        ]
        return {
            "tool_results": results,
            "tool_calls_pending": [],
            "step_count": state.get("step_count", 0) + 1,
        }

    # Execute each tool call through the gateway
    from iara.contracts.tools import ToolInvocationRequest

    results = []
    for tc in pending:
        try:
            request = ToolInvocationRequest(
                invocation_id=str(uuid.uuid4()),
                tool_name=tc["tool_name"],
                arguments=tc.get("arguments", {}),
                tenant_id=uuid.UUID(state["tenant_id"]),
                conversation_id=state["conversation_id"],
                correlation_id=correlation_id,
                idempotency_key=f"{run_id}:{tc['call_id']}",
                call_id=tc["call_id"],
            )
            result = await gateway.invoke(request)
            results.append(
                {
                    "call_id": result.call_id,
                    "tool_name": result.tool_name,
                    "status": result.status,
                    "result_summary": result.result_summary,
                    "draft_ref": result.draft_ref,
                }
            )
        except Exception as exc:
            logger.error(
                "node_tool_executor_error",
                run_id=run_id,
                tool_name=tc.get("tool_name"),
                error_code=type(exc).__name__,
            )
            results.append(
                {
                    "call_id": tc.get("call_id", ""),
                    "tool_name": tc.get("tool_name", ""),
                    "status": "failed",
                    "result_summary": f"Tool execution failed: {type(exc).__name__}",
                }
            )

    logger.info(
        "node_tool_executor_complete",
        run_id=run_id,
        correlation_id=correlation_id,
        results_count=len(results),
    )

    return {
        "tool_results": results,
        "tool_calls_pending": [],
        "step_count": state.get("step_count", 0) + 1,
    }
