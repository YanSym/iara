"""Shared config service registry — single source of truth for per-tenant PublishService instances.

Both the config API router and runtime tools (e.g. kanban) import from here so
they share the same in-memory state without circular imports.
"""

from __future__ import annotations

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


def get_service(tenant_id: str) -> PublishService:
    """Return (or lazily create) the PublishService for a tenant."""
    if tenant_id not in _services:
        _services[tenant_id] = PublishService(tenant_id=tenant_id)
    return _services[tenant_id]


def get_kanban_stages(tenant_id: str) -> list[str]:
    """Return the active kanban stages for a tenant, falling back to defaults."""
    service = _services.get(tenant_id)
    if service is None:
        return _DEFAULT_KANBAN_STAGES
    pub = service.get_active_publication()
    if pub is None:
        return _DEFAULT_KANBAN_STAGES
    return pub.get("config_data", {}).get("kanban_stages") or _DEFAULT_KANBAN_STAGES
