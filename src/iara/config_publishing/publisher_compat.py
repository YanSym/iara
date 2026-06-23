"""Backwards-compatibility shim for synchronous ConfigDraft usage.

Only used by code that cannot call async methods (e.g. some CLI helpers).
New code should use ``PublishService.create_draft()`` (async).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any


class ConfigDraft:
    """Lightweight in-memory draft object (sync, no DB).

    Attributes:
        draft_id: Unique draft identifier.
        tenant_id: Tenant UUID string.
        config_data: Draft configuration data.
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
