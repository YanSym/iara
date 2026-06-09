"""Google Calendar scheduling adapter.

Uses the Google Calendar API v3 freebusy endpoint to determine availability.
Authentication is via a service account JSON key (configured via
``google_calendar_credential_ref``).

The adapter is safe to share across coroutines — a single httpx.AsyncClient
is reused for the lifetime of the worker.

Per INV-04: this adapter only handles READ (availability checks).
Write operations (schedule, cancel, reschedule) always go through the outbox.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from iara.observability.logging import get_logger

logger = get_logger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# Token cache: (access_token, expiry_epoch)
_TokenCache = tuple[str, float]


class GoogleCalendarAdapter:
    """Check availability via Google Calendar API v3 freebusy endpoint.

    Args:
        service_account_json: Service account JSON key (parsed dict).
        timeout_seconds: HTTP request timeout.
    """

    def __init__(
        self,
        service_account_json: dict[str, Any],
        timeout_seconds: int = 15,
    ) -> None:
        self._sa = service_account_json
        self._timeout = timeout_seconds
        self._token_cache: _TokenCache | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "google_calendar"

    @property
    def is_configured(self) -> bool:
        return bool(self._sa.get("private_key") and self._sa.get("client_email"))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _get_access_token(self) -> str:
        """Obtain a valid OAuth2 access token using the service account JWT."""
        now = time.time()
        if self._token_cache and self._token_cache[1] > now + 60:
            return self._token_cache[0]

        token = await self._fetch_token()
        self._token_cache = (token["access_token"], now + token.get("expires_in", 3600))
        return self._token_cache[0]

    async def _fetch_token(self) -> dict[str, Any]:
        """Build and exchange a signed JWT for a Google access token."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise ImportError(
                "cryptography package is required for Google Calendar JWT auth. "
                "Install: pip install cryptography"
            ) from exc

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": self._sa["client_email"],
            "sub": self._sa["client_email"],
            "aud": _GOOGLE_TOKEN_URL,
            "scope": _SCOPE,
            "iat": now,
            "exp": now + 3600,
        }

        def _b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        header_b64 = _b64(json.dumps(header).encode())
        payload_b64 = _b64(json.dumps(payload).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()

        private_key = serialization.load_pem_private_key(
            self._sa["private_key"].encode(), password=None
        )
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[arg-type]
        jwt_token = f"{header_b64}.{payload_b64}.{_b64(signature)}"

        client = await self._get_client()
        response = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            },
        )
        response.raise_for_status()
        return dict(response.json())

    async def check_availability(
        self,
        tenant_id: str,
        date_range_start: str,
        date_range_end: str,
        service_type: str = "general",
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Query Google Calendar freebusy to determine available slots.

        Counts the number of free intervals in the range (each ≥ 30 minutes
        is considered a slot) and returns the first free start time.

        Args:
            tenant_id: Tenant UUID string (for audit logging).
            date_range_start: ISO 8601 datetime (must include timezone).
            date_range_end: ISO 8601 datetime (must include timezone).
            service_type: Service classification (echoed in response).
            calendar_id: Google Calendar ID (default: 'primary').

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
                "error": "service_account_missing",
            }

        try:
            access_token = await self._get_access_token()
            client = await self._get_client()
            response = await client.post(
                _FREEBUSY_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "timeMin": date_range_start,
                    "timeMax": date_range_end,
                    "items": [{"id": calendar_id}],
                },
            )
            response.raise_for_status()
            data = response.json()

            busy_periods = data.get("calendars", {}).get(calendar_id, {}).get("busy", [])
            slot_count = _count_free_slots(date_range_start, date_range_end, busy_periods)
            next_slot = _find_next_free_start(date_range_start, busy_periods)

            logger.info(
                "google_calendar_availability_checked",
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
                "google_calendar_availability_error",
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


def _count_free_slots(
    range_start: str,
    range_end: str,
    busy_periods: list[dict[str, str]],
    slot_duration_minutes: int = 30,
) -> int:
    """Count 30-minute slots that are NOT marked busy."""
    from datetime import datetime, timedelta

    try:
        start = datetime.fromisoformat(range_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(range_end.replace("Z", "+00:00"))
    except ValueError:
        return 0

    busy = []
    for period in busy_periods:
        try:
            bs = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            busy.append((bs, be))
        except (KeyError, ValueError):
            continue

    step = timedelta(minutes=slot_duration_minutes)
    free_count = 0
    cursor = start
    while cursor + step <= end:
        slot_end = cursor + step
        if not any(bs < slot_end and be > cursor for bs, be in busy):
            free_count += 1
        cursor += step

    return free_count


def _find_next_free_start(
    range_start: str,
    busy_periods: list[dict[str, str]],
    slot_duration_minutes: int = 30,
) -> str | None:
    """Return the ISO 8601 start of the first free 30-minute slot."""
    from datetime import datetime, timedelta

    try:
        cursor = datetime.fromisoformat(range_start.replace("Z", "+00:00"))
    except ValueError:
        return None

    busy = sorted(
        [
            (
                datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(p["end"].replace("Z", "+00:00")),
            )
            for p in busy_periods
            if "start" in p and "end" in p
        ],
        key=lambda x: x[0],
    )

    step = timedelta(minutes=slot_duration_minutes)
    for bs, be in busy:
        if cursor + step <= bs:
            return cursor.isoformat()
        if cursor < be:
            cursor = be

    return cursor.isoformat()
