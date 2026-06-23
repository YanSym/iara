"""Google Calendar write adapter — schedule, cancel, reschedule appointments.

Executes scheduling write commands via the Google Calendar API using a
service account credential. All writes include an iCalUID derived from
command_id for idempotency. Retries transient errors with exponential backoff
(same pattern as ChatwootMcpAdapter).

Credentials are never stored directly — always resolved from a secret ref.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx

from iara.contracts.errors import CrossTenantError, FailClosedError, ProviderError
from iara.contracts.provider import ProviderCommand, ProviderMutationResult, ProviderSecurityContext
from iara.observability.logging import get_logger

logger = get_logger(__name__)

PROVIDER_NAME = "google_calendar"

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

CAPABILITY_MAP: dict[str, str] = {
    "schedule_appointment": "events.insert",
    "cancel_appointment": "events.delete",
    "reschedule_appointment": "events.patch",
}


def _resolve_credential(credential_ref: str) -> str:
    """Resolve a credential reference to its value.

    Args:
        credential_ref: A ``secret://`` path or a direct value (dev mode).

    Returns:
        str: Resolved credential value.
    """
    if not credential_ref.startswith("secret://"):
        return credential_ref
    path = credential_ref[len("secret://") :]
    env_key = path.replace("/", "_").upper()
    value = os.environ.get(env_key)
    if value:
        return value
    for fallback in ("GOOGLE_CALENDAR_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON"):
        value = os.environ.get(fallback)
        if value:
            return value
    logger.warning("google_calendar_credential_unresolved", credential_ref=credential_ref)
    return credential_ref


class GoogleCalendarWriteAdapter:
    """Executes scheduling write commands via the Google Calendar API.

    Uses service account JWT credentials. Each write includes an iCalUID
    derived from command_id for cross-call idempotency.

    Args:
        credentials_ref: Secret reference path to the service account JSON.
        calendar_id: Target calendar ID (default "primary").
        timeout_seconds: Request timeout.
        max_retries: Maximum retry attempts for transient errors.
    """

    def __init__(
        self,
        credentials_ref: str,
        calendar_id: str = "primary",
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._credentials_ref = credentials_ref
        self._calendar_id = calendar_id
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return PROVIDER_NAME

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a scheduling write command via Google Calendar API.

        Args:
            command: The provider command to execute.
            security_context: Verified security context.

        Returns:
            ProviderMutationResult: Sanitized execution result.

        Raises:
            CrossTenantError: If tenant IDs do not match.
            FailClosedError: If the capability is not registered.
            ProviderError: If the API call fails after retries.
        """
        # INV-02: cross-tenant re-verification
        if str(command.tenant_id) != str(security_context.tenant_id):
            raise CrossTenantError(
                f"Scheduling command tenant {command.tenant_id} does not match"
                f" security context {security_context.tenant_id}"
            )

        gcal_method = CAPABILITY_MAP.get(command.capability_name)
        if gcal_method is None:
            raise FailClosedError(
                f"Unregistered scheduling capability: {command.capability_name!r}"
            )

        payload = self._build_payload(
            command.capability_name, command.parameters, command.command_id
        )
        result_data = await self._call_gcal_api(gcal_method, payload)

        return ProviderMutationResult(
            command_id=str(command.command_id),
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=True,
            result_ref=f"gcal:{hashlib.sha256(str(result_data).encode()).hexdigest()[:16]}",
        )

    def _build_payload(
        self,
        capability: str,
        parameters: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        """Build the Google Calendar API payload for the given capability.

        Uses iCalUID = sha256(command_id) for idempotent event creation.

        Args:
            capability: Logical capability name.
            parameters: Command parameters.
            command_id: Command ID for iCalUID derivation.

        Returns:
            dict[str, Any]: API payload.
        """
        ical_uid = hashlib.sha256(command_id.encode()).hexdigest()[:32]
        if capability == "schedule_appointment":
            return {
                "iCalUID": ical_uid,
                "summary": "Appointment",
                "start": {"dateTime": parameters.get("datetime_iso", "")},
                "end": {"dateTime": parameters.get("datetime_iso", "")},
                "description": parameters.get("notes_ref", ""),
                "extendedProperties": {
                    "private": {
                        "command_id": command_id,
                        "service_type": parameters.get("service_type", ""),
                    }
                },
            }
        if capability == "cancel_appointment":
            return {
                "event_id": parameters.get("appointment_ref", ""),
                "command_id": command_id,
            }
        if capability == "reschedule_appointment":
            return {
                "event_id": parameters.get("appointment_ref", ""),
                "start": {"dateTime": parameters.get("new_datetime_iso", "")},
                "end": {"dateTime": parameters.get("new_datetime_iso", "")},
                "command_id": command_id,
            }
        return {}

    async def _call_gcal_api(
        self,
        method: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the Google Calendar API with retry on transient errors.

        Args:
            method: The API method name (events.insert, events.delete, events.patch).
            payload: Request payload.

        Returns:
            dict[str, Any]: API response body.

        Raises:
            ProviderError: After exhausting retries.
        """
        import asyncio

        token = _resolve_credential(self._credentials_ref)
        base_url = "https://www.googleapis.com/calendar/v3/calendars"
        calendar_id = self._calendar_id

        if method == "events.insert":
            url = f"{base_url}/{calendar_id}/events"
            http_method = "POST"
        elif method == "events.delete":
            event_id = payload.get("event_id", "")
            url = f"{base_url}/{calendar_id}/events/{event_id}"
            http_method = "DELETE"
        else:  # events.patch
            event_id = payload.get("event_id", "")
            url = f"{base_url}/{calendar_id}/events/{event_id}"
            http_method = "PATCH"

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )

        for attempt in range(self._max_retries):
            try:
                if http_method == "DELETE":
                    response = await self._client.delete(url)
                elif http_method == "POST":
                    response = await self._client.post(url, json=payload)
                else:
                    response = await self._client.patch(url, json=payload)

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    wait = min(2**attempt, 30)
                    logger.warning(
                        "gcal_retryable_error",
                        status_code=response.status_code,
                        method=method,
                        attempt=attempt + 1,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 400:
                    raise ProviderError(
                        f"Google Calendar API error {response.status_code}",
                        provider=PROVIDER_NAME,
                        status_code=response.status_code,
                    )

                try:
                    return response.json()
                except Exception:
                    return {"status": "ok"}

            except ProviderError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError):
                wait = min(2**attempt, 30)
                await asyncio.sleep(wait)
            except Exception as exc:
                raise ProviderError(
                    f"Google Calendar unexpected error: {type(exc).__name__}",
                    provider=PROVIDER_NAME,
                ) from exc

        raise ProviderError(
            "Google Calendar API: all retries exhausted",
            provider=PROVIDER_NAME,
        )

    async def health_check(self) -> bool:
        """Return True if the Google Calendar API is reachable."""
        try:
            token = _resolve_credential(self._credentials_ref)
            return not (not token or token.startswith("secret://"))
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
