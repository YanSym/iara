"""Scheduling adapter protocol.

All scheduling backend adapters implement this interface. The protocol
enforces a consistent API for availability checking; write operations
always go through the outbox (INV-04) and do not require adapters.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SchedulingAdapter(Protocol):
    """Protocol for scheduling availability backends.

    Implementations must be safe to call concurrently and must degrade
    gracefully when credentials are absent or the backend is unreachable.
    """

    @property
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'google_calendar', 'clinicorp')."""
        ...

    @property
    def is_configured(self) -> bool:
        """True if the adapter has the credentials needed to make real calls."""
        ...

    async def check_availability(
        self,
        tenant_id: str,
        date_range_start: str,
        date_range_end: str,
        service_type: str = "general",
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Return sanitized availability data for the given date range.

        The return dict MUST contain:
        - ``available_slots_count`` (int): number of bookable slots
        - ``next_available_slot`` (str | None): ISO 8601 datetime or None
        - ``service_type`` (str): echoed from input
        - ``provider`` (str): identifies the backend that answered

        No contact names, emails, phone numbers, or other PII may appear
        in the result.

        Args:
            tenant_id: Tenant UUID string (for audit logging).
            date_range_start: ISO 8601 date/datetime string.
            date_range_end: ISO 8601 date/datetime string.
            service_type: Optional service classification.
            calendar_id: Provider-specific calendar/resource identifier.

        Returns:
            dict[str, Any]: Sanitized availability summary.
        """
        ...

    async def close(self) -> None:
        """Release any held HTTP connections or resources."""
        ...
