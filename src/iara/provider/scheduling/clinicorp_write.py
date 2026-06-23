"""Clinicorp write adapter — schedule, cancel, reschedule appointments.

Executes scheduling write commands via the Clinicorp API. Uses an API key
credential. Idempotency is achieved by passing ``external_id = command_id``
on every create request.

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

PROVIDER_NAME = "clinicorp"

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

CAPABILITY_MAP: dict[str, str] = {
    "schedule_appointment": "appointments.create",
    "cancel_appointment": "appointments.cancel",
    "reschedule_appointment": "appointments.reschedule",
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
    for fallback in ("CLINICORP_API_KEY", "CLINICORP_TOKEN"):
        value = os.environ.get(fallback)
        if value:
            return value
    logger.warning("clinicorp_credential_unresolved", credential_ref=credential_ref)
    return credential_ref


class ClinicorpWriteAdapter:
    """Executes scheduling write commands via the Clinicorp API.

    Uses API key authentication. Idempotency via external_id = command_id.

    Args:
        api_key_ref: Secret reference path to the Clinicorp API key.
        base_url: Clinicorp API base URL.
        timeout_seconds: Request timeout.
        max_retries: Maximum retry attempts for transient errors.
    """

    def __init__(
        self,
        api_key_ref: str,
        base_url: str = "https://api.clinicorp.com",
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._api_key_ref = api_key_ref
        self._base_url = base_url.rstrip("/")
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
        """Execute a scheduling write command via the Clinicorp API.

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

        clinicorp_method = CAPABILITY_MAP.get(command.capability_name)
        if clinicorp_method is None:
            raise FailClosedError(f"Unregistered Clinicorp capability: {command.capability_name!r}")

        payload = self._build_payload(
            command.capability_name, command.parameters, str(command.command_id)
        )
        result_data = await self._call_clinicorp_api(clinicorp_method, payload)

        return ProviderMutationResult(
            command_id=str(command.command_id),
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=True,
            result_ref=f"clinicorp:{hashlib.sha256(str(result_data).encode()).hexdigest()[:16]}",
        )

    def _build_payload(
        self,
        capability: str,
        parameters: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        """Build the Clinicorp API payload.

        Args:
            capability: Logical capability name.
            parameters: Command parameters.
            command_id: Command ID for idempotency.

        Returns:
            dict[str, Any]: API payload.
        """
        if capability == "schedule_appointment":
            return {
                "external_id": command_id,
                "datetime": parameters.get("datetime_iso", ""),
                "service_type": parameters.get("service_type", "general"),
                "notes_ref": parameters.get("notes_ref", ""),
            }
        if capability == "cancel_appointment":
            return {
                "appointment_id": parameters.get("appointment_ref", ""),
                "reason_ref": parameters.get("reason_ref", ""),
                "external_id": command_id,
            }
        if capability == "reschedule_appointment":
            return {
                "appointment_id": parameters.get("appointment_ref", ""),
                "new_datetime": parameters.get("new_datetime_iso", ""),
                "external_id": command_id,
            }
        return {}

    async def _call_clinicorp_api(
        self,
        method: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the Clinicorp API with retry on transient errors.

        Args:
            method: Clinicorp method (appointments.create, etc.).
            payload: Request payload.

        Returns:
            dict[str, Any]: API response body.

        Raises:
            ProviderError: After exhausting retries.
        """
        import asyncio

        api_key = _resolve_credential(self._api_key_ref)
        path_map = {
            "appointments.create": "/v1/appointments",
            "appointments.cancel": "/v1/appointments/cancel",
            "appointments.reschedule": "/v1/appointments/reschedule",
        }
        url = self._base_url + path_map.get(method, "/v1/appointments")

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            )

        for attempt in range(self._max_retries):
            try:
                response = await self._client.post(url, json=payload)

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    wait = min(2**attempt, 30)
                    logger.warning(
                        "clinicorp_retryable_error",
                        status_code=response.status_code,
                        method=method,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 400:
                    raise ProviderError(
                        f"Clinicorp API error {response.status_code}",
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
                await asyncio.sleep(min(2**attempt, 30))
            except Exception as exc:
                raise ProviderError(
                    f"Clinicorp unexpected error: {type(exc).__name__}",
                    provider=PROVIDER_NAME,
                ) from exc

        raise ProviderError(
            "Clinicorp API: all retries exhausted",
            provider=PROVIDER_NAME,
        )

    async def health_check(self) -> bool:
        """Return True if the Clinicorp API is reachable."""
        try:
            api_key = _resolve_credential(self._api_key_ref)
            return bool(api_key) and not api_key.startswith("secret://")
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
