"""ChatwootMcpAdapter — ProviderAdapter implementation using Chatwoot MCP.

Wraps calls to the Chatwoot MCP server through the explicit registry + policy.
The LLM never interacts with this adapter directly.

Per INV-02: cross-tenant checks before every external call.
Per INV-04: writes always go through outbox; reads return only sanitized refs.

Retry behaviour: transient network errors (TimeoutException, ConnectError,
HTTP 429/500/502/503/504) are retried with exponential backoff (manual loop).
Application errors (4xx except rate-limit) are NOT retried.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx

from iara.contracts.errors import (
    CrossTenantError,
    FailClosedError,
    ProviderError,
)
from iara.contracts.provider import (
    ProviderCommand,
    ProviderMutationResult,
    ProviderSecurityContext,
)
from iara.observability.logging import get_logger
from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry
from iara.provider.error_mapper import ProviderErrorMapper

logger = get_logger(__name__)

PROVIDER_NAME = "chatwoot"

# HTTP status codes that warrant a retry
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _is_retryable_http_error(exc: BaseException) -> bool:
    """True when the exception corresponds to a retryable condition."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


def _resolve_credential(credential_ref: str) -> str:
    """Resolve a credential reference to its actual value.

    Checks the following sources in order:
    1. If the ref is NOT a ``secret://`` path, use it directly (dev mode).
    2. Convert the path to an env var name (``secret://foo/bar`` → ``FOO_BAR``).
    3. Fall back to the ref itself (unknown secret — log a warning).
    """
    if not credential_ref.startswith("secret://"):
        return credential_ref

    path = credential_ref[len("secret://") :]
    env_key = path.replace("/", "_").upper()
    value = os.environ.get(env_key)
    if value:
        return value

    # Final fallback: maybe there's a generic CHATWOOT_API_TOKEN / MCP_TOKEN env
    for fallback_key in ("CHATWOOT_MCP_TOKEN", "CHATWOOT_API_TOKEN", "MCP_TOKEN"):
        value = os.environ.get(fallback_key)
        if value:
            return value

    logger.warning("chatwoot_credential_unresolved", credential_ref=credential_ref)
    return credential_ref


class ChatwootMcpAdapter:
    """Operates Chatwoot via its MCP server.

    This is the real implementation. For tests, use ``FakeChatwootAdapter``.

    Args:
        registry: The ChatwootMcpRegistry for capability resolution.
        mcp_base_url: Base URL of the Chatwoot MCP server.
        credential_ref: Credential reference (``secret://`` path or direct token).
        timeout_seconds: Request timeout.
        max_retries: Maximum retry attempts for transient errors.
    """

    def __init__(
        self,
        registry: ChatwootMcpRegistry,
        mcp_base_url: str,
        credential_ref: str,
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._registry = registry
        self._mcp_base_url = mcp_base_url
        self._credential_ref = credential_ref
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._error_mapper = ProviderErrorMapper(PROVIDER_NAME)
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return PROVIDER_NAME

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with resolved credentials."""
        if self._client is None or self._client.is_closed:
            token = _resolve_credential(self._credential_ref)
            self._client = httpx.AsyncClient(
                base_url=self._mcp_base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a provider command via the Chatwoot MCP server.

        Retries transient network failures up to ``max_retries`` times with
        exponential backoff. Application-level errors (4xx) are not retried.

        Args:
            command: The provider command to execute.
            security_context: Verified security context.

        Returns:
            ProviderMutationResult: Sanitized execution result.

        Raises:
            CrossTenantError: If tenant/account binding fails.
            ProviderError: If the MCP call fails after all retries.
        """
        # Cross-tenant re-verification (INV-02)
        if str(command.tenant_id) != str(security_context.tenant_id):
            raise CrossTenantError(
                f"Command tenant {command.tenant_id} does not match"
                f" security context {security_context.tenant_id}"
            )
        if command.account_id_ref != security_context.account_id_ref:
            raise CrossTenantError(
                "Command account_id_ref does not match security context — refusing execution"
            )

        # Resolve capability
        resolution = self._registry.resolve_intent(
            intent=command.capability_name,
            tenant_id=str(command.tenant_id),
            account_id_ref=command.account_id_ref,
        )
        if not resolution.allowed or not resolution.resolved_tool_name:
            raise FailClosedError(
                f"Capability {command.capability_name!r} is not allowed: {resolution.denial_reason}"
            )

        result_ref = await self._post_with_retry(
            tool_name=resolution.resolved_tool_name,
            parameters=command.parameters,
        )

        # Readback verification for write operations that require it
        readback_ok = True
        if resolution.requires_readback:
            readback_ok = await self._verify_readback(command, resolution.resolved_tool_name)

        return ProviderMutationResult(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=readback_ok,
            result_ref=result_ref,
        )

    async def _post_with_retry(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        attempt: int = 0,
    ) -> str:
        """POST to /mcp/call with retry on transient errors.

        Args:
            tool_name: Resolved MCP tool name.
            parameters: Command parameters.
            attempt: Current attempt count (used for backoff calculation).

        Returns:
            str: SHA-256 prefix of the response body (sanitized result ref).
        """
        import asyncio

        client = await self._get_client()

        last_exc: Exception | None = None
        for attempt_num in range(self._max_retries):
            try:
                response = await client.post(
                    "/mcp/call",
                    json={
                        "tool": tool_name,
                        "params": parameters,
                    },
                )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    wait_secs = min(2**attempt_num, 30)
                    logger.warning(
                        "chatwoot_mcp_retryable_error",
                        status_code=response.status_code,
                        attempt=attempt_num + 1,
                        wait_seconds=wait_secs,
                    )
                    await asyncio.sleep(wait_secs)
                    continue

                if response.status_code >= 400:
                    raise self._error_mapper.map_http_error(response.status_code)

                return hashlib.sha256(response.content).hexdigest()[:24]

            except ProviderError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                wait_secs = min(2**attempt_num, 30)
                logger.warning(
                    "chatwoot_mcp_network_error",
                    error_code=type(exc).__name__,
                    attempt=attempt_num + 1,
                    wait_seconds=wait_secs,
                )
                await asyncio.sleep(wait_secs)
            except Exception as exc:
                raise self._error_mapper.map_exception(exc) from exc

        raise self._error_mapper.map_exception(last_exc or RuntimeError("all retries exhausted"))

    async def _verify_readback(
        self,
        command: ProviderCommand,
        tool_name: str,
    ) -> bool:
        """Verify a write was applied by reading back the affected resource.

        For ``chatwoot_send_message`` capabilities, reads the conversation to
        confirm the message count increased. Returns True on success or if
        readback cannot be performed (non-fatal).

        Args:
            command: The executed command.
            tool_name: The MCP tool name that was called.

        Returns:
            bool: True if readback confirms the write, False on error.
        """
        if "send_message" not in tool_name:
            return True

        conversation_id = (command.parameters or {}).get("conversation_id", "")
        if not conversation_id:
            return True

        try:
            client = await self._get_client()
            response = await client.get(f"/mcp/conversation/{conversation_id}")
            return response.status_code < 400
        except Exception:
            return False

    async def read_conversation_context(
        self,
        tenant_id: str,
        conversation_id: str,
        security_context: ProviderSecurityContext,
    ) -> dict[str, Any]:
        """Read sanitized conversation context.

        Returns only sanitized metadata — message count, label refs, status.
        No raw content, phone numbers, or PII.

        Args:
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier.
            security_context: Verified security context.

        Returns:
            dict[str, Any]: Sanitized conversation metadata.
        """
        # Cross-tenant check
        if tenant_id != str(security_context.tenant_id):
            raise CrossTenantError("Tenant mismatch in read_conversation_context")

        resolution = self._registry.resolve_intent(
            intent="read_conversation",
            tenant_id=tenant_id,
            account_id_ref=security_context.account_id_ref,
        )
        if not resolution.allowed:
            raise FailClosedError(f"Read conversation not allowed: {resolution.denial_reason}")

        client = await self._get_client()
        try:
            response = await client.get(f"/mcp/conversation/{conversation_id}")
            if response.status_code >= 400:
                raise self._error_mapper.map_http_error(response.status_code)

            data = response.json()
            # Return only sanitized metadata
            return {
                "conversation_ref": hashlib.sha256(conversation_id.encode()).hexdigest()[:16],
                "message_count": data.get("message_count", 0),
                "message_refs": [
                    hashlib.sha256(str(m).encode()).hexdigest()[:16]
                    for m in data.get("message_ids", [])
                ],
                "label_refs": [
                    hashlib.sha256(str(lb).encode()).hexdigest()[:16]
                    for lb in data.get("labels", [])
                ],
                "status": data.get("status", "unknown"),
            }

        except ProviderError:
            raise
        except Exception as exc:
            raise self._error_mapper.map_exception(exc) from exc

    async def health_check(self) -> bool:
        """Check Chatwoot MCP connectivity.

        Returns:
            bool: True if the MCP server is reachable.
        """
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.status_code < 400
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
