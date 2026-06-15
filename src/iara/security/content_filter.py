"""Content filter — blocklist-based guard for LLM responses.

Loads ``blocklist.txt`` from the same directory, normalizes all text
(lowercase + accent stripping) before matching, and compiles a single
regex for O(n) scanning regardless of blocklist size.

Usage::

    from iara.security.content_filter import content_filter

    if content_filter.contains_blocked_content(agent_response):
        return BLOCKED_RESPONSE

The singleton ``content_filter`` is loaded once at import time.
Call ``content_filter.reload()`` to pick up edits to the txt file
without restarting the process.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from iara.observability.logging import get_logger

logger = get_logger(__name__)

BLOCKLIST_PATH = Path(__file__).parent / "blocklist.txt"

BLOCKED_RESPONSE = "Desculpe, não posso te ajudar com esse assunto."


def _normalize(text: str) -> str:
    """Lowercase + strip diacritical marks (accents).

    ``unicodedata.normalize('NFD')`` decomposes characters into base + combining
    marks. Filtering out category 'Mn' (Mark, Nonspacing) removes accents,
    leaving only the ASCII base character.
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _load_entries(path: Path) -> list[str]:
    """Read blocklist file and return non-empty, non-comment lines."""
    if not path.exists():
        logger.warning("blocklist_file_not_found", path=str(path))
        return []

    entries = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Normalize the entry itself so it matches normalized input
        normalized = _normalize(line)
        if normalized:
            entries.append(normalized)

    logger.info("blocklist_loaded", entry_count=len(entries), path=str(path))
    return entries


def _compile_pattern(entries: list[str]) -> re.Pattern[str] | None:
    """Build a single compiled regex from all blocklist entries.

    Single-word entries get ``\\b`` word boundaries to avoid false positives
    (e.g., 'pau' inside 'pausa'). Multi-word phrases match as-is.
    """
    if not entries:
        return None

    parts = []
    for entry in entries:
        escaped = re.escape(entry)
        if " " not in entry:
            parts.append(r"\b" + escaped + r"\b")
        else:
            parts.append(escaped)

    return re.compile("|".join(parts))


class ContentFilter:
    """Stateful blocklist filter with lazy-load and hot-reload support.

    Args:
        path: Path to the blocklist txt file.
    """

    def __init__(self, path: Path = BLOCKLIST_PATH) -> None:
        self._path = path
        self._pattern: re.Pattern[str] | None = None
        self._load()

    def _load(self) -> None:
        entries = _load_entries(self._path)
        self._pattern = _compile_pattern(entries)

    def reload(self) -> None:
        """Re-read the blocklist file and recompile the pattern.

        Call this after editing the txt file to apply changes without restart.
        """
        self._load()
        logger.info("blocklist_reloaded")

    def contains_blocked_content(self, text: str) -> bool:
        """Return True if ``text`` contains any blocked entry.

        Normalizes ``text`` before matching so accents and case are irrelevant.

        Args:
            text: The LLM response to inspect.

        Returns:
            bool: True if a blocked term was found.
        """
        if not text or self._pattern is None:
            return False
        normalized = _normalize(text)
        return bool(self._pattern.search(normalized))

    def first_match(self, text: str) -> str | None:
        """Return the first matching blocked term (for logging), or None.

        Never log this in production with PII — use only for internal
        audit/debug logging with redaction enabled.

        Args:
            text: The text to inspect.

        Returns:
            str | None: The matched pattern string, or None.
        """
        if not text or self._pattern is None:
            return None
        normalized = _normalize(text)
        match = self._pattern.search(normalized)
        return match.group(0) if match else None


# Module-level singleton — shared across all requests
content_filter = ContentFilter()
