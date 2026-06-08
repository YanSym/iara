"""Lead search tool handler — lead_search.

Read-only. Returns counts and metadata only — never raw contact lists or PII.
All result sets are capped and anonymized before returning.
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

MAX_RESULTS = 20


async def handle_lead_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search for lead information.

    Returns counts, stage distribution, and anonymized summaries only.
    Raw contact data (names, phones, emails) is never returned (INV-05).

    Args:
        arguments: Tool arguments (search_terms).

    Returns:
        dict[str, Any]: Sanitized search summary.
    """
    search_terms = arguments.get("search_terms", [])

    if not isinstance(search_terms, list):
        search_terms = [str(search_terms)]

    search_terms = [str(t)[:100] for t in search_terms[:10]]  # Cap at 10 terms, 100 chars each

    logger.info(
        "tool_lead_search",
        term_count=len(search_terms),
    )

    # In production: query CRM via ClinicOrp or custom CRM MCP (read-only).
    # Return only counts and anonymized distribution — never raw contact data.
    return {
        "results_count": 0,
        "search_term_count": len(search_terms),
        "stage_distribution": {
            "new_lead": 0,
            "nurturing": 0,
            "qualified": 0,
            "won": 0,
            "lost": 0,
        },
        "note": (
            "Lead search — stub implementation. "
            "Connect CRM MCP for real results. "
            "Result set capped at " + str(MAX_RESULTS) + " items."
        ),
        "pii_redacted": True,
    }
