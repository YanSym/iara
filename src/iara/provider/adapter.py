"""ProviderAdapter protocol — interface adapting a provider/platform to the runtime.

Per the architecture, the LLM never interacts with the ProviderAdapter directly.
It is used internally by the runtime to execute provider commands resolved
through the ChatwootMcpRegistry.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from iara.contracts.provider import (
    ProviderCommand,
    ProviderMutationResult,
    ProviderSecurityContext,
)


@runtime_checkable
class ProviderAdapter(Protocol):
    """Protocol interface for provider/platform adapters.

    All provider integrations must implement this protocol. The runtime
    uses this interface exclusively — it never calls provider APIs directly.

    Implementations:
    - ``ChatwootMcpAdapter``: Operates Chatwoot via MCP.
    - ``FakeChatwootAdapter``: In-memory stub for testing.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider name (e.g. ``chatwoot``).

        Returns:
            str: Lowercase provider name.
        """
        ...

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a provider command and return a sanitized mutation result.

        The implementation must:
        1. Re-verify the security context (cross-tenant check).
        2. Execute the command via the MCP capability.
        3. Perform readback if required.
        4. Return only sanitized results (no raw provider responses).

        Args:
            command: The provider command to execute.
            security_context: Verified security context for cross-tenant check.

        Returns:
            ProviderMutationResult: Sanitized execution result.

        Raises:
            CrossTenantError: If tenant/account binding fails.
            ProviderError: If the provider call fails.
            ReadbackFailedError: If readback cannot confirm the mutation.
        """
        ...

    async def read_conversation_context(
        self,
        tenant_id: str,
        conversation_id: str,
        security_context: ProviderSecurityContext,
    ) -> dict[str, Any]:
        """Read conversation context (read-only, sanitized).

        Returns only sanitized metadata — message counts, label refs,
        status refs. Never raw message content or PII.

        Args:
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier.
            security_context: Verified security context.

        Returns:
            dict[str, Any]: Sanitized conversation metadata.
        """
        ...

    async def health_check(self) -> bool:
        """Check if the provider connection is healthy.

        Returns:
            bool: True if the provider is reachable and authenticated.
        """
        ...
