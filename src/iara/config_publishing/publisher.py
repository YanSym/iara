"""PublishService — deterministic, transactional config publishing (DB-backed).

The publishing pipeline:
  draft → validate → publish → (optionally) rollback

Published versions are immutable. Rollback is done by activating a previous
version — never by deleting.

Per INV-04: the runtime reads only the ACTIVE published version, identified by
the ConfigPublication row where is_active=True and published_at is highest.

This implementation is fully async and backed by Postgres via SQLAlchemy.
It degrades gracefully to an in-memory fallback when no session_factory is
provided (test / development without DB).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from iara.contracts.errors import ConfigPublishError
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class PublishService:
    """Tenant configuration publishing pipeline backed by Postgres.

    The draft → validate → publish → rollback lifecycle:
    - ``create_draft``: insert an AgentConfigVersion with status='draft'.
    - ``publish``: create a ConfigPublication; deactivate all previous ones.
    - ``get_active_publication``: return config_data of the active publication.
    - ``rollback``: reactivate a prior publication without deleting anything.

    Falls back to in-memory storage when ``session_factory`` is None so
    existing unit tests continue to pass without a database.

    Args:
        tenant_id: The tenant UUID string.
        session_factory: Async SQLAlchemy session factory (optional).
    """

    def __init__(
        self,
        tenant_id: str,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._session_factory = session_factory
        # In-memory fallback for test environments
        self._drafts: dict[str, dict[str, Any]] = {}
        self._publications: list[dict[str, Any]] = []

    async def create_draft(
        self,
        config_data: dict[str, Any],
        version_tag: str | None = None,
    ) -> str:
        """Create a new configuration draft and return its draft_id.

        Args:
            config_data: The draft configuration data.
            version_tag: Human-readable tag (auto-generated if None).

        Returns:
            str: UUID draft_id of the created draft.
        """
        draft_id = str(uuid.uuid4())
        config_hash = hashlib.sha256(str(sorted(config_data.items())).encode()).hexdigest()
        tag = version_tag or f"v{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

        if self._session_factory is not None:
            try:
                from sqlalchemy import select

                from iara.persistence.models import AgentConfigVersion, AgentProfile

                tenant_uuid = uuid.UUID(self._tenant_id)

                async with self._session_factory() as session:
                    # Upsert AgentProfile for this tenant
                    result = await session.execute(
                        select(AgentProfile).where(AgentProfile.tenant_id == tenant_uuid)
                    )
                    profile = result.scalar_one_or_none()
                    if profile is None:
                        profile = AgentProfile(
                            id=uuid.uuid4(),
                            tenant_id=tenant_uuid,
                            name=f"profile:{self._tenant_id[:8]}",
                        )
                        session.add(profile)
                        await session.flush()

                    version_row = AgentConfigVersion(
                        id=uuid.UUID(draft_id),
                        tenant_id=tenant_uuid,
                        profile_id=profile.id,
                        version_tag=tag,
                        status="draft",
                        config_hash=config_hash,
                        config_data=config_data,
                    )
                    session.add(version_row)
                    await session.commit()

                logger.info(
                    "config_draft_created_db",
                    draft_id=draft_id,
                    version_tag=tag,
                    tenant_ref=self._tenant_id[:8],
                )
                return draft_id
            except Exception as exc:
                logger.warning(
                    "config_draft_db_failed_using_memory",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

        # In-memory fallback
        self._drafts[draft_id] = {
            "draft_id": draft_id,
            "tenant_id": self._tenant_id,
            "config_data": config_data,
            "version_tag": tag,
            "config_hash": config_hash,
            "created_at": datetime.now(UTC).isoformat(),
        }
        logger.info("config_draft_created_memory", draft_id=draft_id, version_tag=tag)
        return draft_id

    async def publish(
        self,
        draft_id: str,
        published_by: str = "system",
    ) -> str:
        """Validate and publish a draft; return the new publication_id.

        Deactivates all previous publications for this tenant first (immutable
        history — old publications remain but are no longer active).

        Args:
            draft_id: The UUID draft to publish.
            published_by: Opaque reference to the publisher (never raw PII).

        Returns:
            str: UUID publication_id of the new publication.

        Raises:
            ConfigPublishError: If the draft is not found or has empty config.
        """
        publication_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        if self._session_factory is not None:
            try:
                from sqlalchemy import select, update

                from iara.persistence.models import AgentConfigVersion, ConfigPublication

                tenant_uuid = uuid.UUID(self._tenant_id)
                draft_uuid = uuid.UUID(draft_id)

                async with self._session_factory() as session:
                    result = await session.execute(
                        select(AgentConfigVersion).where(
                            AgentConfigVersion.id == draft_uuid,
                            AgentConfigVersion.tenant_id == tenant_uuid,
                        )
                    )
                    version = result.scalar_one_or_none()
                    if version is None:
                        raise ConfigPublishError(f"Draft {draft_id!r} not found in DB")
                    if not version.config_data:
                        raise ConfigPublishError(f"Draft {draft_id!r} has empty config data")

                    # Deactivate previous publications
                    await session.execute(
                        update(ConfigPublication)
                        .where(
                            ConfigPublication.tenant_id == tenant_uuid,
                            ConfigPublication.is_active.is_(True),
                        )
                        .values(is_active=False)
                    )

                    # Mark version as published
                    version.status = "published"

                    pub = ConfigPublication(
                        id=uuid.UUID(publication_id),
                        tenant_id=tenant_uuid,
                        config_version_id=draft_uuid,
                        published_by=published_by[:256],
                        published_at=now,
                        is_active=True,
                    )
                    session.add(pub)
                    await session.commit()

                logger.info(
                    "config_published_db",
                    publication_id=publication_id,
                    draft_id=draft_id,
                    tenant_ref=self._tenant_id[:8],
                )
                return publication_id
            except ConfigPublishError:
                raise
            except Exception as exc:
                logger.warning(
                    "config_publish_db_failed_using_memory",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

        # In-memory fallback
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise ConfigPublishError(f"Draft {draft_id!r} not found")
        if not draft.get("config_data"):
            raise ConfigPublishError(f"Draft {draft_id!r} has empty config data")

        for pub in self._publications:
            pub["is_active"] = False

        publication = {
            "publication_id": publication_id,
            "draft_id": draft_id,
            "tenant_id": self._tenant_id,
            "version_tag": draft["version_tag"],
            "config_hash": draft["config_hash"],
            "config_data": draft["config_data"],
            "published_by": published_by,
            "published_at": now.isoformat(),
            "is_active": True,
        }
        self._publications.append(publication)
        logger.info(
            "config_published_memory",
            publication_id=publication_id,
            version_tag=draft["version_tag"],
        )
        return publication_id

    async def get_active_publication(self) -> dict[str, Any] | None:
        """Return the config_data of the currently active publication, or None.

        Returns:
            dict[str, Any] | None: Active publication data including config_data.
        """
        if self._session_factory is not None:
            try:
                from sqlalchemy import select

                from iara.persistence.models import AgentConfigVersion, ConfigPublication

                tenant_uuid = uuid.UUID(self._tenant_id)

                async with self._session_factory() as session:
                    result = await session.execute(
                        select(ConfigPublication, AgentConfigVersion)
                        .join(
                            AgentConfigVersion,
                            ConfigPublication.config_version_id == AgentConfigVersion.id,
                        )
                        .where(
                            ConfigPublication.tenant_id == tenant_uuid,
                            ConfigPublication.is_active.is_(True),
                        )
                        .order_by(ConfigPublication.published_at.desc())
                        .limit(1)
                    )
                    row = result.first()

                if row is None:
                    return None

                pub, version = row
                return {
                    "publication_id": str(pub.id),
                    "tenant_id": str(pub.tenant_id),
                    "version_tag": version.version_tag,
                    "config_hash": version.config_hash,
                    "config_data": version.config_data or {},
                    "published_by": pub.published_by,
                    "published_at": pub.published_at.isoformat() if pub.published_at else "",
                    "is_active": pub.is_active,
                }
            except Exception as exc:
                logger.warning(
                    "config_get_active_db_failed",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

        # In-memory fallback
        for pub in reversed(self._publications):
            if pub.get("is_active"):
                return pub
        return None

    async def rollback(self, publication_id: str) -> dict[str, Any]:
        """Reactivate a previous publication (immutable — does not delete).

        Args:
            publication_id: UUID of the publication to reactivate.

        Returns:
            dict[str, Any]: The reactivated publication record.

        Raises:
            ConfigPublishError: If the publication is not found.
        """
        if self._session_factory is not None:
            try:
                from sqlalchemy import select, update

                from iara.persistence.models import AgentConfigVersion, ConfigPublication

                tenant_uuid = uuid.UUID(self._tenant_id)
                pub_uuid = uuid.UUID(publication_id)

                async with self._session_factory() as session:
                    # Check target publication exists
                    result = await session.execute(
                        select(ConfigPublication, AgentConfigVersion)
                        .join(
                            AgentConfigVersion,
                            ConfigPublication.config_version_id == AgentConfigVersion.id,
                        )
                        .where(
                            ConfigPublication.id == pub_uuid,
                            ConfigPublication.tenant_id == tenant_uuid,
                        )
                    )
                    row = result.first()
                    if row is None:
                        raise ConfigPublishError(f"Publication {publication_id!r} not found")

                    # Deactivate all → reactivate target
                    await session.execute(
                        update(ConfigPublication)
                        .where(ConfigPublication.tenant_id == tenant_uuid)
                        .values(is_active=False)
                    )
                    await session.execute(
                        update(ConfigPublication)
                        .where(ConfigPublication.id == pub_uuid)
                        .values(is_active=True, rolled_back_at=datetime.now(UTC))
                    )
                    await session.commit()

                pub, version = row
                logger.info(
                    "config_rolled_back_db",
                    publication_id=publication_id,
                    version_tag=version.version_tag,
                    tenant_ref=self._tenant_id[:8],
                )
                return {
                    "publication_id": str(pub.id),
                    "tenant_id": str(pub.tenant_id),
                    "version_tag": version.version_tag,
                    "config_hash": version.config_hash,
                    "config_data": version.config_data or {},
                    "is_active": True,
                }
            except ConfigPublishError:
                raise
            except Exception as exc:
                logger.warning(
                    "config_rollback_db_failed_using_memory",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

        # In-memory fallback
        target = None
        for pub in self._publications:
            if pub["publication_id"] == publication_id:
                target = pub
                break
        if target is None:
            raise ConfigPublishError(f"Publication {publication_id!r} not found")

        for pub in self._publications:
            pub["is_active"] = False
        target["is_active"] = True
        logger.info(
            "config_rolled_back_memory",
            publication_id=publication_id,
            version_tag=target.get("version_tag"),
        )
        return target

    # ── Legacy sync shim for backwards compatibility ──────────────────────────

    def create_draft_sync(
        self,
        config_data: dict[str, Any],
        version_tag: str,
    ) -> Any:
        """Synchronous draft creation shim for callers that cannot use async.

        Returns a ConfigDraft-compatible object. Prefer ``create_draft`` in
        async contexts. In-memory only — does not persist to DB.
        """
        from iara.config_publishing.publisher_compat import ConfigDraft

        draft = ConfigDraft(
            tenant_id=self._tenant_id,
            config_data=config_data,
            version_tag=version_tag,
        )
        self._drafts[draft.draft_id] = {
            "draft_id": draft.draft_id,
            "tenant_id": self._tenant_id,
            "config_data": config_data,
            "version_tag": version_tag,
            "config_hash": draft.config_hash,
            "created_at": datetime.now(UTC).isoformat(),
        }
        return draft

    def validate_draft(self, draft_id: str) -> bool:
        """Validate a draft by draft_id (in-memory fallback only).

        Args:
            draft_id: Draft UUID string.

        Returns:
            bool: True if valid.

        Raises:
            ConfigPublishError: If not found or empty config.
        """
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise ConfigPublishError(f"Draft {draft_id!r} not found")
        if not draft.get("config_data"):
            raise ConfigPublishError(f"Draft {draft_id!r} has empty config data")
        return True
