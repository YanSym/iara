"""ChatwootMcpAdapter — ProviderAdapter implementation using Chatwoot MCP.

Wraps calls to the Chatwoot MCP server through the explicit registry + policy.
The LLM never interacts with this adapter directly.

Per INV-02: cross-tenant checks before every external call.
Per INV-04: writes always go through outbox; reads return only sanitized refs.
"""

from __future__ import annotations

import hashlib
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


class ChatwootMcpAdapter:
    """Operates Chatwoot via its MCP server.

    This is the real implementation. For tests, use ``FakeChatwootAdapter``.

    Args:
        registry: The ChatwootMcpRegistry for capability resolution.
        mcp_base_url: Base URL of the Chatwoot MCP server.
        credential_ref: Reference to the credential in the secret store.
        timeout_seconds: Request timeout.
        max_retries: Maximum retry attempts.
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
        self._max_retries = max_retries
        self._error_mapper = ProviderErrorMapper(PROVIDER_NAME)
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return PROVIDER_NAME

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Returns:
            httpx.AsyncClient: The HTTP client.
        """
        if self._client is None or self._client.is_closed:
            # In production, resolve the credential from the secret store.
            # Here we use the ref as a placeholder.
            self._client = httpx.AsyncClient(
                base_url=self._mcp_base_url,
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._credential_ref}"},
            )
        return self._client

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a provider command via the Chatwoot MCP server.

        Args:
            command: The provider command to execute.
            security_context: Verified security context.

        Returns:
            ProviderMutationResult: Sanitized execution result.

        Raises:
            CrossTenantError: If tenant/account binding fails.
            ProviderError: If the MCP call fails.
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

        # Execute via MCP
        client = await self._get_client()
        try:
            response = await client.post(
                "/mcp/call",
                json={
                    "tool": resolution.resolved_tool_name,
                    "params": command.parameters,
                },
            )
            if response.status_code >= 400:
                raise self._error_mapper.map_http_error(response.status_code)

            # Extract result ref (sanitized — no raw response data)
            result_ref = hashlib.sha256(response.content).hexdigest()[:24]

        except ProviderError:
            raise
        except Exception as exc:
            raise self._error_mapper.map_exception(exc) from exc

        return ProviderMutationResult(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=not resolution.requires_readback,
            result_ref=result_ref,
        )

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
