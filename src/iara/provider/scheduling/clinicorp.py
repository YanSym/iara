"""Clinicorp scheduling adapter.

Queries the Clinicorp REST API for appointment availability.
Authentication is via an API key resolved from the secret store.

Only the availability READ endpoint is implemented here.
Write operations (schedule, cancel) go through the outbox.
"""

from __future__ import annotations

from typing import Any

import httpx

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Clinicorp API paths — adjust if the endpoint differs in your tenant's plan
_AVAILABILITY_PATH = "/v1/appointments/availability"


class ClinicorpAdapter:
    """Check availability via Clinicorp REST API.

    Args:
        base_url: Clinicorp API base URL.
        api_key: API key for authentication.
        timeout_seconds: HTTP request timeout.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 15,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "clinicorp"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and not self._api_key.startswith("secret://"))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def check_availability(
        self,
        tenant_id: str,
        date_range_start: str,
        date_range_end: str,
        service_type: str = "general",
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Query Clinicorp for available appointment slots.

        Args:
            tenant_id: Tenant UUID string (for audit logging).
            date_range_start: ISO 8601 date or datetime string.
            date_range_end: ISO 8601 date or datetime string.
            service_type: Service/specialty type.
            calendar_id: Clinicorp resource/room identifier.

        Returns:
            dict[str, Any]: Sanitized availability summary.
        """
        if not self.is_configured:
            return {
                "available_slots_count": 0,
                "next_available_slot": None,
                "service_type": service_type,
                "provider": self.provider_name,
                "configured": False,
                "error": "api_key_missing",
            }

        try:
            client = await self._get_client()
            response = await client.get(
                _AVAILABILITY_PATH,
                params={
                    "start_date": date_range_start,
                    "end_date": date_range_end,
                    "service_type": service_type,
                    "resource_id": calendar_id,
                    "limit": 50,
                },
            )
            response.raise_for_status()
            data = response.json()

            # Clinicorp returns {"slots": [{"start": ..., "end": ...}, ...]}
            slots = data.get("slots") or data.get("available_slots") or []
            slot_count = len(slots)
            next_slot = slots[0].get("start") if slots else None

            logger.info(
                "clinicorp_availability_checked",
                tenant_ref=tenant_id[:8],
                service_type=service_type,
                slot_count=slot_count,
            )
            return {
                "available_slots_count": slot_count,
                "next_available_slot": next_slot,
                "service_type": service_type,
                "provider": self.provider_name,
                "configured": True,
            }

        except Exception as exc:
            logger.warning(
                "clinicorp_availability_error",
                tenant_ref=tenant_id[:8],
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )
            return {
                "available_slots_count": 0,
                "next_available_slot": None,
                "service_type": service_type,
                "provider": self.provider_name,
                "configured": True,
                "error": type(exc).__name__,
            }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
