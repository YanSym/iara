"""Governed semantic memory store.

Memory is disabled by default (``IARA_MEMORY_ENABLED=false``). When enabled,
all items are stored with namespace, TTL, consent flag, and redaction.

Per the spec: checkpoint store is separate from semantic memory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iara.observability.logging import get_logger

logger = get_logger(__name__)


class MemoryItem:
    """A single governed memory item.

    Attributes:
        key: Unique key within the namespace.
        namespace: Tenant + scope namespace.
        content: Sanitized memory content (no PII).
        ttl_days: Time-to-live in days.
        created_at: Creation timestamp.
        expires_at: Expiry timestamp.
        consent_ref: Reference to the consent record.
        is_anonymized: Whether the item has been anonymized.
    """

    def __init__(
        self,
        key: str,
        namespace: str,
        content: str,
        ttl_days: int = 90,
        consent_ref: str | None = None,
    ) -> None:
        self.key = key
        self.namespace = namespace
        self.content = content
        self.ttl_days = ttl_days
        self.created_at = datetime.now(UTC)
        self.expires_at = self.created_at + timedelta(days=ttl_days)
        self.consent_ref = consent_ref
        self.is_anonymized = False


class MemoryStore:
    """Governed in-memory store for semantic memory items.

    When memory is disabled, all operations are no-ops.

    Args:
        tenant_id: The tenant UUID string.
        namespace: Memory namespace (default: tenant_id).
        enabled: Whether memory is enabled.
        ttl_days: Default TTL for memory items.
    """

    def __init__(
        self,
        tenant_id: str,
        namespace: str | None = None,
        enabled: bool = False,
        ttl_days: int = 90,
    ) -> None:
        self._tenant_id = tenant_id
        self._namespace = namespace or tenant_id
        self._enabled = enabled
        self._ttl_days = ttl_days
        self._items: dict[str, MemoryItem] = {}

    async def store(
        self,
        key: str,
        content: str,
        consent_ref: str | None = None,
    ) -> bool:
        """Store a memory item.

        Args:
            key: Unique key within the namespace.
            content: Sanitized memory content.
            consent_ref: Reference to the consent record.

        Returns:
            bool: True if stored, False if memory is disabled.
        """
        if not self._enabled:
            logger.debug("memory_disabled_skip_store", key=key)
            return False

        item = MemoryItem(
            key=key,
            namespace=self._namespace,
            content=content,
            ttl_days=self._ttl_days,
            consent_ref=consent_ref,
        )
        self._items[key] = item
        logger.info("memory_stored", key=key, namespace=self._namespace)
        return True

    async def retrieve(self, key: str) -> MemoryItem | None:
        """Retrieve a memory item by key.

        Returns None if memory is disabled, the item is expired, or not found.

        Args:
            key: The item key.

        Returns:
            MemoryItem | None: The item, or None.
        """
        if not self._enabled:
            return None

        item = self._items.get(key)
        if item is None:
            return None

        # Check expiry
        if datetime.now(UTC) > item.expires_at:
            del self._items[key]
            return None

        return item

    async def purge(self, key: str) -> bool:
        """Permanently delete a memory item (for LGPD purge requests).

        Args:
            key: The item key.

        Returns:
            bool: True if deleted.
        """
        if key in self._items:
            del self._items[key]
            logger.info("memory_purged", key=key, namespace=self._namespace)
            return True
        return False

    async def anonymize(self, key: str) -> bool:
        """Anonymize a memory item (replace content with placeholder).

        Args:
            key: The item key.

        Returns:
            bool: True if anonymized.
        """
        item = self._items.get(key)
        if item is None:
            return False

        item.content = "[ANONYMIZED]"
        item.is_anonymized = True
        logger.info("memory_anonymized", key=key, namespace=self._namespace)
        return True
