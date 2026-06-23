"""HITL node — persists human-in-the-loop hold and halts the graph.

When ``hitl_requested=True`` reaches this node the graph:
1. Persists a HitlHoldRecord to Postgres (if a session factory is injected).
2. Returns the escalation response that was already set by the guardrails node.
3. Sets ``hitl_requested=True`` so the graph routes to END.

The node is a no-op on persistence if ``session_factory`` is None — the
hold is not durable but the escalation response is still returned. This
allows the graph to run in test environments without a real DB.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import partial
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def hitl_node(
    state: dict[str, Any],
    *,
    session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Persist a HITL hold and signal graph termination.

    Args:
        state: Current graph state.
        session_factory: Optional async SQLAlchemy session factory for DB writes.

    Returns:
        dict[str, Any]: State updates (hitl_requested remains True → routes to END).
    """
    run_id = str(state.get("run_id", uuid.uuid4()))
    tenant_id = str(state.get("tenant_id", ""))
    conversation_id = str(state.get("conversation_id", ""))
    correlation_id = str(state.get("correlation_id", ""))
    hitl_reason = str(state.get("hitl_reason", "unknown"))

    log = logger.bind(
        run_id=run_id,
        tenant_ref=tenant_id[:8] if tenant_id else "",
        conversation_id=conversation_id,
        hitl_reason=hitl_reason,
    )
    log.info("hitl_node_triggered")

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                await repo.register(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    thread_id=run_id,
                    reason=hitl_reason,
                    context_snapshot={
                        "step_count": state.get("step_count", 0),
                        "correlation_id": correlation_id,
                        "hitl_reason": hitl_reason,
                    },
                )
                await session.commit()
            log.info("hitl_hold_persisted")
        except Exception as exc:
            log.warning(
                "hitl_hold_persist_failed",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    return {
        "hitl_requested": True,
        "step_count": state.get("step_count", 0) + 1,
    }


def build_hitl_node(session_factory: Any | None = None) -> Any:
    """Build the hitl_node with optional DB session factory injected.

    Args:
        session_factory: Optional async SQLAlchemy session factory.

    Returns:
        Callable: An async node function.
    """
    return partial(hitl_node, session_factory=session_factory)
