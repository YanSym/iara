"""Null scheduling adapter — graceful fallback when no backend is configured.

Returns a descriptive result rather than fake data so the LLM can tell the
contact that scheduling is not yet available instead of hallucinating slots.
"""

from __future__ import annotations

from typing import Any


class NullSchedulingAdapter:
    """Returns an informative 'not configured' response instead of fake data.

    Used when neither Google Calendar nor Clinicorp credentials are present.
    """

    @property
    def provider_name(self) -> str:
        return "null"

    @property
    def is_configured(self) -> bool:
        return False

    async def check_availability(
        self,
        tenant_id: str,
        date_range_start: str,
        date_range_end: str,
        service_type: str = "general",
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Return a 'not configured' response with zero slots."""
        return {
            "available_slots_count": 0,
            "next_available_slot": None,
            "service_type": service_type,
            "provider": self.provider_name,
            "configured": False,
            "message": (
                "Serviço de agendamento não está configurado. "
                "Entre em contato com a equipe de suporte para verificar disponibilidade."
            ),
        }

    async def close(self) -> None:
        pass
