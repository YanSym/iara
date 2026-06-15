"""PublishService — deterministic, transactional config publishing.

The publishing pipeline:
  draft → validate → review/HITL → publish

Published versions are immutable. Rollback is done by activating a previous
version — never by deleting.

Per INV-04: the runtime reads only the ACTIVE published version.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from iara.contracts.errors import ConfigPublishError
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class ConfigDraft:
    """A draft configuration pending review and publication.

    Attributes:
        draft_id: Unique draft identifier.
        tenant_id: Tenant UUID string.
        config_data: Draft configuration data (sanitized).
        version_tag: Human-readable version tag.
        created_at: Creation timestamp.
        config_hash: SHA-256 hash of the config data.
    """

    def __init__(
        self,
        tenant_id: str,
        config_data: dict[str, Any],
        version_tag: str,
    ) -> None:
        self.draft_id = str(uuid.uuid4())
        self.tenant_id = tenant_id
        self.config_data = config_data
        self.version_tag = version_tag
        self.created_at = datetime.now(UTC)
        self.config_hash = hashlib.sha256(str(sorted(config_data.items())).encode()).hexdigest()


class PublishService:
    """Manages the configuration draft → publish pipeline.

    This service is deterministic and transactional. Published versions
    are immutable — rollback activates a previous version without deletion.

    Args:
        tenant_id: The tenant UUID string.
    """

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._drafts: dict[str, ConfigDraft] = {}
        self._publications: list[dict[str, Any]] = []

    def create_draft(
        self,
        config_data: dict[str, Any],
        version_tag: str,
    ) -> ConfigDraft:
        """Create a new configuration draft.

        Args:
            config_data: The draft configuration data.
            version_tag: Human-readable version tag.

        Returns:
            ConfigDraft: The created draft.
        """
        draft = ConfigDraft(
            tenant_id=self._tenant_id,
            config_data=config_data,
            version_tag=version_tag,
        )
        self._drafts[draft.draft_id] = draft
        logger.info(
            "config_draft_created",
            draft_id=draft.draft_id,
            version_tag=version_tag,
        )
        return draft

    def validate_draft(self, draft_id: str) -> bool:
        """Validate a draft configuration.

        Args:
            draft_id: The draft to validate.

        Returns:
            bool: True if valid.

        Raises:
            ConfigPublishError: If the draft is not found or invalid.
        """
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise ConfigPublishError(f"Draft {draft_id!r} not found")
        if not draft.config_data:
            raise ConfigPublishError(f"Draft {draft_id!r} has empty config data")
        return True

    def publish(
        self,
        draft_id: str,
        published_by: str,
    ) -> dict[str, Any]:
        """Publish a validated draft configuration.

        Published versions are immutable. The runtime will begin using
        the new config for all new runs after this call.

        Args:
            draft_id: The draft to publish.
            published_by: Reference to the user/system publishing (opaque ref).

        Returns:
            dict[str, Any]: Publication record with version ref.

        Raises:
            ConfigPublishError: If the draft cannot be published.
        """
        self.validate_draft(draft_id)
        draft = self._drafts[draft_id]

        # Deactivate previous publications
        for pub in self._publications:
            pub["is_active"] = False

        publication = {
            "publication_id": str(uuid.uuid4()),
            "draft_id": draft_id,
            "tenant_id": self._tenant_id,
            "version_tag": draft.version_tag,
            "config_hash": draft.config_hash,
            "config_data": draft.config_data,
            "published_by": published_by,
            "published_at": datetime.now(UTC).isoformat(),
            "is_active": True,
        }
        self._publications.append(publication)

        logger.info(
            "config_published",
            publication_id=publication["publication_id"],
            version_tag=draft.version_tag,
            config_hash=draft.config_hash[:12],
        )
        return publication

    def get_active_publication(self) -> dict[str, Any] | None:
        """Return the currently active publication.

        Returns:
            dict[str, Any] | None: Active publication record, or None.
        """
        for pub in reversed(self._publications):
            if pub.get("is_active"):
                return pub
        return None

    def rollback_to(self, publication_id: str) -> dict[str, Any]:
        """Rollback by activating a previous publication.

        Does NOT delete any versions. Only changes which version is active.

        Args:
            publication_id: The publication to activate.

        Returns:
            dict[str, Any]: The reactivated publication.

        Raises:
            ConfigPublishError: If the publication is not found.
        """
        target = None
        for pub in self._publications:
            if pub["publication_id"] == publication_id:
                target = pub
                break

        if target is None:
            raise ConfigPublishError(f"Publication {publication_id!r} not found")

        # Deactivate current, activate target
        for pub in self._publications:
            pub["is_active"] = False
        target["is_active"] = True

        logger.info(
            "config_rolled_back",
            publication_id=publication_id,
            version_tag=target["version_tag"],
        )
        return target
