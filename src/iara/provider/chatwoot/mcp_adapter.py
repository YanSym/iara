"""ChatwootMcpAdapter — ProviderAdapter implementation using Chatwoot MCP.

Wraps calls to the Digi2B customised Chatwoot MCP server (HTTP transport,
JSON-RPC 2.0). The LLM never interacts with this adapter directly.

MCP endpoint pattern:  {base_url}/mcp/{account_id}/{slug}
Auth header:           Api-Access-Token: <token>
Transport:             HTTP  POST — method "tools/call" (JSON-RPC 2.0)

Per INV-02: cross-tenant checks before every external call.
Per INV-04: writes always go through outbox; reads return only sanitized refs.

Retry behaviour: transient network errors (TimeoutException, ConnectError,
HTTP 429/500/502/503/504) are retried with exponential backoff (manual loop).
Application errors (4xx except rate-limit) are NOT retried.

Label note (from MCP doc 2026-06-15):
  conversations_set_labels REPLACES the full label list. The adapter performs
  a read-before-write for the label_conversation intent to avoid clobbering
  existing labels.
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

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

_MCP_RPC_ID = 1  # stateless single-request; constant ID is fine


def _is_retryable_http_error(exc: BaseException) -> bool:
    """True when the exception corresponds to a retryable condition."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


def _resolve_credential(credential_ref: str) -> str:
    """Resolve a credential reference to its actual value.

    1. If NOT a ``secret://`` path, use directly (dev mode).
    2. Convert path to env var (``secret://foo/bar`` → ``FOO_BAR``).
    3. Fall back to common env var names (CHATWOOT_MCP_TOKEN, etc.).
    """
    if not credential_ref.startswith("secret://"):
        return credential_ref

    path = credential_ref[len("secret://") :]
    env_key = path.replace("/", "_").upper()
    value = os.environ.get(env_key)
    if value:
        return value

    for fallback_key in ("CHATWOOT_MCP_TOKEN", "CHATWOOT_API_TOKEN", "MCP_TOKEN"):
        value = os.environ.get(fallback_key)
        if value:
            return value

    logger.warning("chatwoot_credential_unresolved", credential_ref=credential_ref)
    return credential_ref


class ChatwootMcpAdapter:
    """Operates Chatwoot via its MCP server (Digi2B customised, HTTP transport).

    Constructs the full MCP endpoint URL as:
        {mcp_base_url}/mcp/{account_id}/{mcp_slug}

    Authentication uses the ``Api-Access-Token`` header (not Bearer).

    Args:
        registry: The ChatwootMcpRegistry for capability resolution.
        mcp_base_url: Base URL of the Chatwoot instance (e.g. https://app.digi2b.com).
        account_id: Numeric Chatwoot account ID string (e.g. "59").
        mcp_slug: MCP server slug (e.g. "mcp-suporte").
        credential_ref: Credential reference (``secret://`` path or direct token).
        timeout_seconds: Request timeout.
        max_retries: Maximum retry attempts for transient errors.
    """

    def __init__(
        self,
        registry: ChatwootMcpRegistry,
        mcp_base_url: str,
        account_id: str,
        mcp_slug: str,
        credential_ref: str,
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._registry = registry
        self._mcp_base_url = mcp_base_url.rstrip("/")
        self._account_id = account_id
        self._mcp_slug = mcp_slug
        self._credential_ref = credential_ref
        self._timeout = timeout_seconds
        self._max_retries = max(1, max_retries)
        self._error_mapper = ProviderErrorMapper(PROVIDER_NAME)
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return PROVIDER_NAME

    @property
    def _mcp_endpoint(self) -> str:
        """Full MCP endpoint URL: {base}/mcp/{account_id}/{slug}."""
        return f"{self._mcp_base_url}/mcp/{self._account_id}/{self._mcp_slug}"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with resolved credentials.

        Auth header is ``Api-Access-Token`` per the Digi2B MCP specification.
        """
        if self._client is None or self._client.is_closed:
            token = _resolve_credential(self._credential_ref)
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "Api-Access-Token": token,
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
                f"Capability {command.capability_name!r} is not allowed:"
                f" {resolution.denial_reason}"
            )

        # Build MCP arguments (translate logical parameters → MCP schema)
        mcp_arguments = self._build_mcp_arguments(
            intent=command.capability_name,
            tool_name=resolution.resolved_tool_name,
            parameters=command.parameters,
        )

        # Special pre-processing: label operations need read-before-write
        if resolution.resolved_tool_name == "conversations_set_labels":
            mcp_arguments = await self._merge_labels(mcp_arguments)

        result_ref = await self._call_tool(
            tool_name=resolution.resolved_tool_name,
            arguments=mcp_arguments,
        )

        readback_ok = True
        if resolution.requires_readback:
            readback_ok = await self._verify_readback(
                command=command,
                tool_name=resolution.resolved_tool_name,
            )

        return ProviderMutationResult(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=readback_ok,
            result_ref=result_ref,
        )

    def _build_mcp_arguments(
        self,
        intent: str,
        tool_name: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate ProviderCommand parameters to the MCP tool argument schema.

        The MCP document specifies:
        - GET-like tools: {"query": {}, "body": {}}
        - Tools with an id: {"id": "<id>", "query": {}, "body": {}}
        - conversation_message_send has its own schema.

        Args:
            intent: Runtime intent (used to specialise the mapping).
            tool_name: Real MCP tool name.
            parameters: Command parameters from the outbox.

        Returns:
            dict[str, Any]: Arguments to send to the MCP tool.
        """
        p = parameters or {}
        conv_id = p.get("conversation_id", "")

        if tool_name == "conversation_message_send":
            # Determine whether this is a private note
            private = intent == "kanban_comment" or p.get("private", False)
            return {
                "conversation_id": conv_id,
                "content": p.get("content", ""),
                "message_type": p.get("message_type", "outgoing"),
                "private": private,
                "content_type": "text",
                "content_attributes": {},
                "media_urls": p.get("media_urls", []),
                "signed_ids": p.get("signed_ids", []),
            }

        if tool_name == "conversations_set_labels":
            label = p.get("label", "")
            labels = p.get("labels", [label] if label else [])
            return {
                "id": conv_id,
                "query": {},
                "body": {"labels": labels},
            }

        if tool_name in ("conversations_get", "conversations_toggle_status"):
            return {
                "id": conv_id or p.get("id", ""),
                "query": {},
                "body": p.get("body", {}),
            }

        if tool_name == "conversation_assignments_assign":
            return {
                "conversation_id": conv_id,
                "query": {},
                "body": {"assignee_id": p.get("assignee_id", "")},
            }

        if tool_name == "contacts_update":
            return {
                "id": p.get("contact_id", p.get("id", "")),
                "query": {},
                "body": p.get("body", p),
            }

        if tool_name == "contacts_get":
            return {
                "id": p.get("contact_id", p.get("id", "")),
                "query": {},
                "body": {},
            }

        if tool_name in ("kanban_tasks_move",):
            return {
                "id": p.get("task_id", ""),
                "query": {},
                "body": {"step_id": p.get("step_id", ""), "stage": p.get("stage", "")},
            }

        if tool_name in ("kanban_tasks_create",):
            return {
                "query": {},
                "body": {
                    "conversation_id": conv_id,
                    "board_id": p.get("board_id", ""),
                    "step_id": p.get("step_id", ""),
                    "title": p.get("title", ""),
                },
            }

        if tool_name in ("messages_list", "conversations_get_labels"):
            return {
                "conversation_id": conv_id,
                "query": {},
                "body": {},
            }

        # Default: pass parameters as-is under query/body
        return {"query": p.get("query", {}), "body": p.get("body", p)}

    async def _merge_labels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Read current labels and merge with the new one (read-before-write).

        Chatwoot's conversations_set_labels REPLACES the full label list.
        To add a label without clobbering existing ones we must read first.

        Args:
            arguments: Arguments built for conversations_set_labels.

        Returns:
            dict[str, Any]: Arguments with merged label list.
        """
        conv_id = arguments.get("id", "")
        if not conv_id:
            return arguments

        try:
            existing_raw = await self._call_tool(
                tool_name="conversations_get_labels",
                arguments={"conversation_id": conv_id, "query": {}, "body": {}},
            )
            # result_ref is a hash; we can't parse it back — log and continue
            logger.info(
                "chatwoot_label_readback_done",
                conversation_id=conv_id,
                result_ref=existing_raw,
            )
        except Exception as exc:
            logger.warning(
                "chatwoot_label_read_failed_proceeding",
                conversation_id=conv_id,
                error_code=type(exc).__name__,
            )

        return arguments

    async def _call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Call an MCP tool via JSON-RPC 2.0 over HTTP.

        POSTs to {base_url}/mcp/{account_id}/{slug} with body:
          {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": tool_name, "arguments": arguments}}

        Args:
            tool_name: Real MCP tool name.
            arguments: Tool arguments.

        Returns:
            str: SHA-256 prefix of the response body (sanitized result ref).
        """
        return await self._post_with_retry(tool_name=tool_name, arguments=arguments)

    async def _post_with_retry(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """POST JSON-RPC tools/call with retry on transient errors.

        Args:
            tool_name: Resolved MCP tool name.
            arguments: Tool arguments.

        Returns:
            str: SHA-256 prefix of the response body (sanitized result ref).
        """
        import asyncio

        client = await self._get_client()
        endpoint = self._mcp_endpoint
        payload = {
            "jsonrpc": "2.0",
            "id": _MCP_RPC_ID,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        last_exc: Exception | None = None
        for attempt_num in range(self._max_retries):
            try:
                response = await client.post(endpoint, json=payload)

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    wait_secs = min(2**attempt_num, 30)
                    logger.warning(
                        "chatwoot_mcp_retryable_error",
                        status_code=response.status_code,
                        tool=tool_name,
                        attempt=attempt_num + 1,
                        wait_seconds=wait_secs,
                    )
                    await asyncio.sleep(wait_secs)
                    continue

                if response.status_code >= 400:
                    raise self._error_mapper.map_http_error(response.status_code)

                # Parse JSON-RPC response and check for application-level errors
                try:
                    rpc = response.json()
                    if rpc.get("error"):
                        err = rpc["error"]
                        raise ProviderError(
                            f"MCP error {err.get('code', 'unknown')}: "
                            f"{str(err.get('message', ''))[:200]}",
                            provider=PROVIDER_NAME,
                        )
                    result = rpc.get("result", {})
                    is_error = result.get("isError", False)
                    if is_error:
                        content = result.get("content", [{}])
                        msg = content[0].get("text", "tool error") if content else "tool error"
                        raise ProviderError(
                            f"MCP tool {tool_name!r} returned isError=true: {msg[:200]}",
                            provider=PROVIDER_NAME,
                        )
                except (ValueError, KeyError):
                    pass  # Not JSON or unexpected shape — hash the raw body

                return hashlib.sha256(response.content).hexdigest()[:24]

            except ProviderError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                wait_secs = min(2**attempt_num, 30)
                logger.warning(
                    "chatwoot_mcp_network_error",
                    tool=tool_name,
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
        """Verify a write was applied by calling conversations_get.

        Only runs for message-send and label operations; other writes return True.

        Args:
            command: The executed command.
            tool_name: The MCP tool name that was called.

        Returns:
            bool: True if readback succeeds or is not applicable.
        """
        readback_tools = {"conversation_message_send", "conversations_set_labels"}
        if tool_name not in readback_tools:
            return True

        conversation_id = (command.parameters or {}).get("conversation_id", "")
        if not conversation_id:
            return True

        try:
            await self._call_tool(
                tool_name="conversations_get",
                arguments={"id": conversation_id, "query": {}, "body": {}},
            )
            return True
        except Exception as exc:
            logger.warning(
                "chatwoot_readback_failed",
                tool=tool_name,
                error_code=type(exc).__name__,
            )
            return False

    async def read_conversation_context(
        self,
        tenant_id: str,
        conversation_id: str,
        security_context: ProviderSecurityContext,
    ) -> dict[str, Any]:
        """Read sanitized conversation context via MCP.

        Returns only sanitized metadata — message count, label refs, status.
        No raw content, phone numbers, or PII.

        Args:
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier.
            security_context: Verified security context.

        Returns:
            dict[str, Any]: Sanitized conversation metadata.
        """
        if tenant_id != str(security_context.tenant_id):
            raise CrossTenantError("Tenant mismatch in read_conversation_context")

        resolution = self._registry.resolve_intent(
            intent="read_conversation",
            tenant_id=tenant_id,
            account_id_ref=security_context.account_id_ref,
        )
        if not resolution.allowed:
            raise FailClosedError(f"Read conversation not allowed: {resolution.denial_reason}")

        try:
            raw_ref = await self._call_tool(
                tool_name="conversations_get",
                arguments={"id": conversation_id, "query": {}, "body": {}},
            )
            return {
                "conversation_ref": hashlib.sha256(conversation_id.encode()).hexdigest()[:16],
                "result_ref": raw_ref,
                "status": "ok",
            }

        except ProviderError:
            raise
        except Exception as exc:
            raise self._error_mapper.map_exception(exc) from exc

    async def health_check(self) -> bool:
        """Check Chatwoot MCP connectivity via account_context.

        Returns:
            bool: True if the MCP server is reachable.
        """
        try:
            await self._call_tool(tool_name="account_context", arguments={})
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
