"""HITL (Human-in-the-Loop) router — approve or reject paused agent runs.

When the agent sets ``state["hitl_requested"] = True``, the LangGraph edge
routes through the ``hitl`` node (which persists the hold to Postgres), then
ends. The run is frozen at its last checkpoint.

This router exposes endpoints to:
  1. List pending holds from Postgres (primary) or in-memory (graceful degradation).
  2. Approve: update LangGraph state + resume the run from its checkpoint.
  3. Reject: log the decision; run remains ended.

The compiled graph must be registered via ``HitlHoldRegistry.set_graph()``
at application startup (see app.py lifespan). Without a registered graph,
approve/reject are accepted but the run is not automatically resumed
(it can be resumed externally via the checkpointer).

DB persistence uses ``request.app.state.db_session_factory`` if available.
If the DB is not configured (development/test), falls back to in-memory gracefully.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from iara.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["hitl"])


# ── In-memory graph registry (still needed for graph resumption) ──────────────


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
    """In-memory graph registry for HITL run resumption.

    Holds are primarily persisted to Postgres via HitlHoldRepository.
    This registry is retained solely to hold the compiled LangGraph graph
    reference for run resumption (``ainvoke``, ``aupdate_state``).
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
        """Cache an in-memory copy of the hold for graph resumption lookups."""
        hold = HitlHold(
            run_id=run_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            reason=reason,
            requested_at=datetime.now(UTC).isoformat(),
        )
        self._holds[run_id] = hold
        return hold

    def get(self, run_id: str) -> HitlHold | None:
        return self._holds.get(run_id)

    def list_pending(self) -> list[HitlHold]:
        return [h for h in self._holds.values() if h.status == "pending"]

    async def resume_graph(self, run_id: str, thread_id: str) -> None:
        """Update state and resume the LangGraph run from its checkpoint.

        Args:
            run_id: The run identifier.
            thread_id: The LangGraph thread_id (checkpointer key).
        """
        if self._graph is None:
            logger.warning("hitl_graph_not_registered", run_id=run_id)
            return
        try:
            config = {"configurable": {"thread_id": thread_id}}
            await self._graph.aupdate_state(
                config,
                {"hitl_requested": False, "hitl_approved": True},
            )
            await self._graph.ainvoke(None, config)
            logger.info("hitl_run_resumed", run_id=run_id, thread_id=thread_id)
        except Exception as exc:
            logger.error(
                "hitl_resume_failed",
                run_id=run_id,
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )


# Module-level singleton for graph resumption
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


# ── DB helpers ────────────────────────────────────────────────────────────────


def _get_session_factory(request: Request) -> Any | None:
    """Return the DB session factory from app state, or None if not configured."""
    return getattr(getattr(request, "app", None), "state", None) and getattr(
        request.app.state, "db_session_factory", None
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new HITL hold for a paused run (manual override)",
)
async def register_hold(body: HitlRegisterRequest, request: Request) -> HitlHold:
    """Register a paused run that requires human approval.

    The graph HITL node auto-registers holds in Postgres. This endpoint is
    provided as a manual override when the hold must be registered externally.
    """
    run_id = str(uuid.uuid4())
    session_factory = _get_session_factory(request)

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                await repo.register(
                    run_id=run_id,
                    tenant_id=body.tenant_id,
                    conversation_id=body.conversation_id,
                    thread_id=body.thread_id,
                    reason=body.reason,
                )
                await session.commit()
            logger.info("hitl_hold_registered_db", run_id=run_id)
        except Exception as exc:
            logger.warning(
                "hitl_hold_register_db_failed",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    # Always cache in memory for graph resumption lookups.
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
async def list_pending(request: Request) -> list[HitlHold]:
    """Return all holds currently awaiting a decision.

    Reads from Postgres when available; falls back to in-memory.
    """
    session_factory = _get_session_factory(request)

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                records = await repo.list_pending_all()

            return [
                HitlHold(
                    run_id=str(r.run_id),
                    tenant_id=str(r.tenant_id),
                    conversation_id=str(r.conversation_id),
                    thread_id=str(r.thread_id),
                    reason=r.reason,
                    status=str(r.status),
                    requested_at=r.requested_at.isoformat() if r.requested_at else "",
                    resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
                    resolved_by=r.resolved_by,
                )
                for r in records
            ]
        except Exception as exc:
            logger.warning(
                "hitl_list_pending_db_failed",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    return _registry.list_pending()


@router.get(
    "/{run_id}",
    summary="Get a specific HITL hold by run_id",
)
async def get_hold(run_id: str, request: Request) -> HitlHold:
    """Return a specific hold by run_id."""
    session_factory = _get_session_factory(request)

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                record = await repo.get(run_id)

            if record is not None:
                return HitlHold(
                    run_id=str(record.run_id),
                    tenant_id=str(record.tenant_id),
                    conversation_id=str(record.conversation_id),
                    thread_id=str(record.thread_id),
                    reason=record.reason,
                    status=str(record.status),
                    requested_at=record.requested_at.isoformat() if record.requested_at else "",
                    resolved_at=record.resolved_at.isoformat() if record.resolved_at else None,
                    resolved_by=record.resolved_by,
                )
        except Exception as exc:
            logger.warning(
                "hitl_get_db_failed",
                run_id=run_id,
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

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
async def approve_hold(run_id: str, body: HitlDecisionRequest, request: Request) -> HitlHold:
    """Approve a HITL hold.

    Persists the approval to Postgres, updates LangGraph state, and resumes
    the run from its last checkpoint.
    """
    session_factory = _get_session_factory(request)
    thread_id: str | None = None

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                record = await repo.approve(run_id=run_id, approved_by=body.decided_by)
                await session.commit()

            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"HITL hold {run_id!r} not found or already resolved",
                )
            thread_id = str(record.thread_id)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "hitl_approve_db_failed",
                run_id=run_id,
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    # Update in-memory registry and resume graph
    mem_hold = _registry.get(run_id)
    if mem_hold is None:
        mem_hold = HitlHold(
            run_id=run_id,
            tenant_id="",
            conversation_id="",
            thread_id=thread_id or run_id,
            requested_at=datetime.now(UTC).isoformat(),
        )
        _registry._holds[run_id] = mem_hold  # noqa: SLF001

    if mem_hold.status != "pending" and session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Hold {run_id!r} is already {mem_hold.status!r}",
        )

    mem_hold.status = "approved"
    mem_hold.resolved_at = datetime.now(UTC).isoformat()
    mem_hold.resolved_by = body.decided_by
    logger.info("hitl_approved", run_id=run_id, approved_by=body.decided_by[:32])

    await _registry.resume_graph(run_id=run_id, thread_id=thread_id or mem_hold.thread_id)
    return mem_hold


@router.post(
    "/{run_id}/reject",
    summary="Reject a paused run — run remains ended at its checkpoint",
)
async def reject_hold(run_id: str, body: HitlDecisionRequest, request: Request) -> HitlHold:
    """Reject a HITL hold.

    The run is left at its END checkpoint. No further processing occurs.
    The rejection is logged for audit.
    """
    session_factory = _get_session_factory(request)

    if session_factory is not None:
        try:
            from iara.persistence.repositories.hitl_holds import HitlHoldRepository

            async with session_factory() as session:
                repo = HitlHoldRepository(session)
                record = await repo.reject(
                    run_id=run_id,
                    rejected_by=body.decided_by,
                    reason=body.reason,
                )
                await session.commit()

            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"HITL hold {run_id!r} not found or already resolved",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "hitl_reject_db_failed",
                run_id=run_id,
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    mem_hold = _registry.get(run_id)
    if mem_hold is None and session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HITL hold {run_id!r} not found",
        )

    if mem_hold is not None:
        if mem_hold.status != "pending" and session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Hold {run_id!r} is already {mem_hold.status!r}",
            )
        mem_hold.status = "rejected"
        mem_hold.resolved_at = datetime.now(UTC).isoformat()
        mem_hold.resolved_by = body.decided_by
        if body.reason:
            mem_hold.reason = body.reason
    else:
        mem_hold = HitlHold(
            run_id=run_id,
            tenant_id="",
            conversation_id="",
            thread_id=run_id,
            status="rejected",
            reason=body.reason,
            requested_at=datetime.now(UTC).isoformat(),
            resolved_at=datetime.now(UTC).isoformat(),
            resolved_by=body.decided_by,
        )

    logger.info("hitl_rejected", run_id=run_id, rejected_by=body.decided_by[:32])
    return mem_hold
