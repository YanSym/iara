"""Config router — tenant runtime configuration pipeline.

Exposes the draft → publish pipeline so external backends (e.g. Breno's system)
can push persona, business hours, Kanban stages and active tools without a deploy.

Endpoints:
  POST /config/{tenant_id}/draft              Create a new config draft
  POST /config/{tenant_id}/draft/{draft_id}/publish  Publish a validated draft
  GET  /config/{tenant_id}/active             Read the currently active config
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from iara.config_publishing.registry import get_service
from iara.config_publishing.schema import TenantConfig
from iara.contracts.errors import ConfigPublishError
from iara.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["config"])


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/{tenant_id}/draft",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new configuration draft for a tenant",
)
async def create_draft(tenant_id: str, config: TenantConfig) -> dict[str, Any]:
    """Receive a TenantConfig payload and create a versioned draft.

    The draft is not yet active — call ``/publish`` to activate it.
    The config data is stored as-is; the runtime reads only the ACTIVE
    publication (per INV-04).

    Args:
        tenant_id: The tenant UUID string.
        config: Full or partial tenant config payload.

    Returns:
        dict: Draft metadata including ``draft_id`` and ``config_hash``.
    """
    service = get_service(tenant_id)
    draft = service.create_draft(
        config_data=config.model_dump(),
        version_tag=config.version_tag,
    )
    logger.info(
        "config_draft_created_via_api",
        tenant_id=tenant_id,
        draft_id=draft.draft_id,
        version_tag=draft.version_tag,
    )
    return {
        "draft_id": draft.draft_id,
        "tenant_id": tenant_id,
        "version_tag": draft.version_tag,
        "config_hash": draft.config_hash,
        "created_at": draft.created_at.isoformat(),
        "status": "draft",
    }


@router.post(
    "/{tenant_id}/draft/{draft_id}/publish",
    summary="Publish a validated draft — activates it for the runtime",
)
async def publish_draft(
    tenant_id: str,
    draft_id: str,
    published_by: str = "api",
) -> dict[str, Any]:
    """Validate and publish a draft configuration.

    Once published, the runtime uses this config for all new runs on this
    tenant. Previous publications are deactivated but not deleted (rollback
    is always possible).

    Args:
        tenant_id: The tenant UUID string.
        draft_id: The draft to publish (from ``create_draft`` response).
        published_by: Opaque identifier of who triggered the publish.

    Returns:
        dict: Publication record with ``publication_id`` and ``is_active``.
    """
    service = get_service(tenant_id)
    try:
        publication = service.publish(draft_id=draft_id, published_by=published_by)
    except ConfigPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    logger.info(
        "config_published_via_api",
        tenant_id=tenant_id,
        publication_id=publication["publication_id"],
        version_tag=publication["version_tag"],
    )
    return publication


@router.get(
    "/{tenant_id}/active",
    summary="Return the currently active configuration for a tenant",
)
async def get_active_config(tenant_id: str) -> dict[str, Any]:
    """Return the active published configuration.

    Returns 404 if no config has been published yet for this tenant.

    Args:
        tenant_id: The tenant UUID string.

    Returns:
        dict: Active publication record including the full config data.
    """
    service = get_service(tenant_id)
    pub = service.get_active_publication()
    if pub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active configuration for tenant {tenant_id!r}",
        )
    return pub


@router.post(
    "/{tenant_id}/rollback/{publication_id}",
    summary="Roll back to a previous published configuration",
)
async def rollback_config(tenant_id: str, publication_id: str) -> dict[str, Any]:
    """Activate a previous publication without deleting the current one.

    Args:
        tenant_id: The tenant UUID string.
        publication_id: The publication to reactivate.

    Returns:
        dict: The reactivated publication record.
    """
    service = get_service(tenant_id)
    try:
        pub = service.rollback_to(publication_id=publication_id)
    except ConfigPublishError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    logger.info(
        "config_rolled_back_via_api",
        tenant_id=tenant_id,
        publication_id=publication_id,
    )
    return pub
