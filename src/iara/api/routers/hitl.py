"""HITL (Human-in-the-Loop) router — approve or reject paused agent runs.

When the agent sets ``state["hitl_requested"] = True``, the LangGraph edge
routes the run to END and the run is frozen at its last checkpoint.

This router exposes two endpoints that:
  1. Look up the pending hold in the HitlHoldRegistry.
  2. Update the LangGraph state via ``aupdate_state`` (clears the interrupt,
     sets the decision).
  3. Resume the graph via ``ainvoke(None, config)`` (approve only).
  4. Persist the hold status in the registry.

The compiled graph must be registered via ``HitlHoldRegistry.set_graph()``
at application startup (see app.py lifespan). Without a registered graph,
approve/reject are still accepted but the run cannot be automatically resumed
(it can be resumed externally via the checkpointer).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from iara.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["hitl"])


# ── Hold registry ─────────────────────────────────────────────────────────────


class HitlHold(BaseModel):
    """A paused agent run awaiting human approval."""

    run_id: str
    tenant_id: str
    conversation_id: str
    thread_id: str
    reason: str | None = None
    status: str = "pending"  # pending | approved | rejected
    requested_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None


class HitlHoldRegistry:
    """In-memory registry of pending HITL holds.

    In production this should be backed by the ``hitl_holds`` Postgres table
    (migration 0003). The in-memory implementation is sufficient for Phase 5
    because holds are short-lived within a single worker process.

    The compiled LangGraph graph is injected via ``set_graph()`` at startup
    so that ``approve()`` can resume the run directly.
    """

    def __init__(self) -> None:
        self._holds: dict[str, HitlHold] = {}
        self._graph: Any = None

    def set_graph(self, graph: Any) -> None:
        """Register the compiled LangGraph graph for run resumption."""
        self._graph = graph

    def register(
        self,
        run_id: str,
        tenant_id: str,
        conversation_id: str,
        thread_id: str,
        reason: str | None = None,
    ) -> HitlHold:
        """Register a new HITL hold for a paused run."""
        hold = HitlHold(
            run_id=run_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            reason=reason,
            requested_at=datetime.now(UTC).isoformat(),
        )
        self._holds[run_id] = hold
        logger.info(
            "hitl_hold_registered",
            run_id=run_id,
            tenant_ref=tenant_id[:8],
            conversation_id=conversation_id,
        )
        return hold

    def get(self, run_id: str) -> HitlHold | None:
        return self._holds.get(run_id)

    def list_pending(self) -> list[HitlHold]:
        return [h for h in self._holds.values() if h.status == "pending"]

    async def approve(self, run_id: str, approved_by: str) -> HitlHold:
        """Approve a hold and attempt to resume the graph run."""
        hold = self._holds.get(run_id)
        if hold is None:
            raise KeyError(run_id)
        if hold.status != "pending":
            raise ValueError(f"Hold {run_id!r} is already {hold.status!r}")

        hold.status = "approved"
        hold.resolved_at = datetime.now(UTC).isoformat()
        hold.resolved_by = approved_by

        logger.info("hitl_approved", run_id=run_id, approved_by=approved_by[:32])

        if self._graph is not None:
            try:
                config = {"configurable": {"thread_id": hold.thread_id}}
                await self._graph.aupdate_state(
                    config,
                    {"hitl_requested": False, "hitl_approved": True},
                )
                await self._graph.ainvoke(None, config)
                logger.info("hitl_run_resumed", run_id=run_id, thread_id=hold.thread_id)
            except Exception as exc:
                logger.error(
                    "hitl_resume_failed",
                    run_id=run_id,
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

        return hold

    async def reject(self, run_id: str, rejected_by: str, reason: str | None = None) -> HitlHold:
        """Reject a hold — the run remains ended at its checkpoint."""
        hold = self._holds.get(run_id)
        if hold is None:
            raise KeyError(run_id)
        if hold.status != "pending":
            raise ValueError(f"Hold {run_id!r} is already {hold.status!r}")

        hold.status = "rejected"
        hold.resolved_at = datetime.now(UTC).isoformat()
        hold.resolved_by = rejected_by
        if reason:
            hold.reason = reason

        logger.info("hitl_rejected", run_id=run_id, rejected_by=rejected_by[:32])
        return hold


# Singleton — shared across requests via app.state or direct import
_registry = HitlHoldRegistry()


def get_registry() -> HitlHoldRegistry:
    return _registry


# ── Request / Response schemas ────────────────────────────────────────────────


class HitlRegisterRequest(BaseModel):
    tenant_id: str
    conversation_id: str
    thread_id: str
    reason: str | None = None


class HitlDecisionRequest(BaseModel):
    decided_by: str
    reason: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new HITL hold for a paused run",
)
async def register_hold(body: HitlRegisterRequest) -> HitlHold:
    """Register a paused run that requires human approval.

    Called by the graph worker when ``state["hitl_requested"]`` is True.
    """
    run_id = str(uuid.uuid4())
    hold = _registry.register(
        run_id=run_id,
        tenant_id=body.tenant_id,
        conversation_id=body.conversation_id,
        thread_id=body.thread_id,
        reason=body.reason,
    )
    return hold


@router.get(
    "/pending",
    summary="List all pending HITL holds",
)
async def list_pending() -> list[HitlHold]:
    """Return all holds currently awaiting a decision."""
    return _registry.list_pending()


@router.get(
    "/{run_id}",
    summary="Get a specific HITL hold by run_id",
)
async def get_hold(run_id: str) -> HitlHold:
    hold = _registry.get(run_id)
    if hold is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL hold {run_id!r} not found",
        )
    return hold


@router.post(
    "/{run_id}/approve",
    summary="Approve a paused run — resumes the graph from its checkpoint",
)
async def approve_hold(run_id: str, body: HitlDecisionRequest) -> HitlHold:
    """Approve a HITL hold.

    Sets the hold status to ``approved``, updates the LangGraph state, and
    resumes the run from its last checkpoint if a compiled graph is registered.
    """
    try:
        hold = await _registry.approve(run_id=run_id, approved_by=body.decided_by)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL hold {run_id!r} not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return hold


@router.post(
    "/{run_id}/reject",
    summary="Reject a paused run — run remains ended at its checkpoint",
)
async def reject_hold(run_id: str, body: HitlDecisionRequest) -> HitlHold:
    """Reject a HITL hold.

    The run is left at its END checkpoint. No further processing occurs.
    The rejection is logged for audit.
    """
    try:
        hold = await _registry.reject(
            run_id=run_id,
            rejected_by=body.decided_by,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL hold {run_id!r} not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return hold
