"""Shared config service registry — single source of truth for per-tenant PublishService instances.

Both the config API router and runtime tools (e.g. kanban) import from here so
they share the same in-memory state without circular imports.

``get_kanban_stages`` is synchronous for backwards compatibility with catalog
modules. It reads from the last known active publication (cached in memory) and
falls back to the default stage list.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from iara.config_publishing.publisher import PublishService

_DEFAULT_KANBAN_STAGES = [
    "new_lead",
    "contacted",
    "nurturing",
    "qualified",
    "proposal_sent",
    "negotiation",
    "won",
    "lost",
]

_services: dict[str, PublishService] = {}


def get_service(
    tenant_id: str,
    session_factory: Callable[..., Any] | None = None,
) -> PublishService:
    """Return (or create) the PublishService for *tenant_id*.

    When *session_factory* is provided and the service for this tenant is not
    yet in the cache, creates a DB-backed service. If the service already
    exists in cache but lacks a session factory, the existing instance is
    returned without modification — callers should ensure consistent wiring.

    Args:
        tenant_id: Tenant UUID string.
        session_factory: Optional async SQLAlchemy session factory.

    Returns:
        PublishService: The (possibly new) service instance.
    """
    if tenant_id not in _services:
        _services[tenant_id] = PublishService(
            tenant_id=tenant_id,
            session_factory=session_factory,
        )
    return _services[tenant_id]


def get_kanban_stages(tenant_id: str) -> list[str]:
    """Return the active kanban stages for a tenant, falling back to defaults.

    Synchronous — reads the in-memory publication cache. Call this from
    synchronous catalog handlers. For the authoritative DB-backed version,
    use ``await service.get_active_publication()`` directly.

    Args:
        tenant_id: Tenant UUID string.

    Returns:
        list[str]: Ordered list of Kanban stage slugs.
    """
    service = _services.get(tenant_id)
    if service is None:
        return _DEFAULT_KANBAN_STAGES

    # Check in-memory publications (sync path — no DB call)
    pub: dict[str, Any] | None = None
    for p in reversed(service._publications):  # noqa: SLF001
        if p.get("is_active"):
            pub = p
            break

    if pub is None:
        return _DEFAULT_KANBAN_STAGES
    return pub.get("config_data", {}).get("kanban_stages") or _DEFAULT_KANBAN_STAGES
