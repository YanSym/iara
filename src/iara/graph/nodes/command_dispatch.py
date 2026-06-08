"""Command dispatch node — enqueues provider commands to the outbox.

This node writes the final response command to the outbox. The outbox drainer
worker executes it asynchronously.

Per INV-04: provider commands are NEVER executed directly in a replayable node.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def command_dispatch_node(
    state: dict[str, Any],
    outbox_service: Any = None,
) -> dict[str, Any]:
    """Enqueue the response command to the provider outbox.

    Args:
        state: Current graph state.
        outbox_service: Outbox service (injected; optional in tests).

    Returns:
        dict[str, Any]: State updates with response_sent flag.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")
    agent_response = state.get("agent_response")

    if not agent_response or state.get("response_sent", False):
        return {"step_count": state.get("step_count", 0) + 1}

    logger.info(
        "node_command_dispatch_start",
        run_id=run_id,
        correlation_id=correlation_id,
    )

    if outbox_service is None:
        # Test stub — simulate enqueueing
        logger.info("command_dispatch_stub", run_id=run_id)
        return {
            "response_sent": True,
            "step_count": state.get("step_count", 0) + 1,
        }

    try:
        command_id = str(uuid.uuid4())
        await outbox_service.enqueue(
            command_id=command_id,
            tenant_id=state["tenant_id"],
            conversation_id=state["conversation_id"],
            capability_name="send_message",
            parameters={"content": agent_response},
            correlation_id=correlation_id,
            idempotency_key=f"response:{run_id}",
        )

        logger.info(
            "node_command_dispatch_complete",
            run_id=run_id,
            correlation_id=correlation_id,
            command_id=command_id,
        )

        return {
            "response_sent": True,
            "step_count": state.get("step_count", 0) + 1,
        }

    except Exception as exc:
        logger.error(
            "node_command_dispatch_failed",
            run_id=run_id,
            correlation_id=correlation_id,
            error_code=type(exc).__name__,
        )
        return {
            "error": f"Command dispatch failed: {type(exc).__name__}",
            "step_count": state.get("step_count", 0) + 1,
        }
