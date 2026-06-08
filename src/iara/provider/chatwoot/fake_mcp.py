"""FakeChatwootAdapter — in-memory stub for testing and local development.

This adapter simulates Chatwoot MCP behavior without making real network calls.
All tests and local development use this adapter by default.

Per the plan: the fake adapter validates inputs, refuses invalid payloads,
and simulates readback — it does not just silently accept everything.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from iara.contracts.errors import CrossTenantError, FailClosedError, ProviderError
from iara.contracts.provider import (
    ProviderCommand,
    ProviderMutationResult,
    ProviderSecurityContext,
)
from iara.observability.logging import get_logger
from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry

logger = get_logger(__name__)


class FakeChatwootAdapter:
    """In-memory stub implementation of the Chatwoot MCP adapter.

    Stores state in memory. Validates inputs and simulates readback.
    Safe to use in tests without any real network infrastructure.

    Args:
        registry: The ChatwootMcpRegistry for capability resolution.
        simulate_failures: If True, randomly fail some operations (for resilience testing).
    """

    def __init__(
        self,
        registry: ChatwootMcpRegistry,
        simulate_failures: bool = False,
    ) -> None:
        self._registry = registry
        self._simulate_failures = simulate_failures
        # In-memory conversation store: {conversation_id: {messages: [], labels: [], status: str}}
        self._conversations: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"messages": [], "labels": [], "status": "open"}
        )
        self._executed_commands: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "chatwoot"

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Simulate executing a provider command.

        Args:
            command: The command to simulate.
            security_context: Verified security context.

        Returns:
            ProviderMutationResult: Simulated mutation result.

        Raises:
            CrossTenantError: If tenant/account binding fails.
            ProviderError: If the simulated operation fails.
        """
        # Cross-tenant check
        if str(command.tenant_id) != str(security_context.tenant_id):
            raise CrossTenantError("FakeChatwootAdapter: tenant mismatch — fail-closed")
        if command.account_id_ref != security_context.account_id_ref:
            raise CrossTenantError("FakeChatwootAdapter: account mismatch — fail-closed")

        # Validate capability exists in registry
        resolution = self._registry.resolve_intent(
            intent=command.capability_name,
            tenant_id=str(command.tenant_id),
            account_id_ref=command.account_id_ref,
        )
        if not resolution.allowed:
            raise FailClosedError(
                f"FakeChatwootAdapter: capability {command.capability_name!r} not allowed"
            )

        # Validate required parameters
        if not command.parameters:
            raise ProviderError(
                f"FakeChatwootAdapter: command {command.capability_name!r} requires parameters",
                provider="chatwoot",
            )

        # Record the command
        self._executed_commands.append(
            {
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
                "capability_name": command.capability_name,
                "tenant_id": str(command.tenant_id),
            }
        )

        # Simulate the mutation
        result_ref = hashlib.sha256(command.command_id.encode()).hexdigest()[:24]

        # Apply to in-memory state
        conversation_id = command.parameters.get("conversation_id", "unknown")
        if command.capability_name == "send_message":
            self._conversations[conversation_id]["messages"].append(result_ref)
        elif command.capability_name == "add_label":
            label = command.parameters.get("label", "unknown")
            self._conversations[conversation_id]["labels"].append(
                hashlib.sha256(label.encode()).hexdigest()[:16]
            )

        return ProviderMutationResult(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=True,  # Fake always confirms
            result_ref=result_ref,
        )

    async def read_conversation_context(
        self,
        tenant_id: str,
        conversation_id: str,
        security_context: ProviderSecurityContext,
    ) -> dict[str, Any]:
        """Return sanitized fake conversation context.

        Args:
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier.
            security_context: Verified security context.

        Returns:
            dict[str, Any]: Sanitized fake conversation metadata.
        """
        if tenant_id != str(security_context.tenant_id):
            raise CrossTenantError("FakeChatwootAdapter: tenant mismatch in read")

        conv = self._conversations[conversation_id]
        return {
            "conversation_ref": hashlib.sha256(conversation_id.encode()).hexdigest()[:16],
            "message_count": len(conv["messages"]),
            "message_refs": list(conv["messages"]),
            "label_refs": list(conv["labels"]),
            "status": conv["status"],
        }

    async def health_check(self) -> bool:
        """Always returns True for the fake adapter."""
        return True

    def get_executed_commands(self) -> list[dict[str, Any]]:
        """Return list of executed commands for assertion in tests.

        Returns:
            list[dict[str, Any]]: Executed command records.
        """
        return list(self._executed_commands)

    def reset(self) -> None:
        """Reset all in-memory state (for test isolation)."""
        self._conversations.clear()
        self._executed_commands.clear()
