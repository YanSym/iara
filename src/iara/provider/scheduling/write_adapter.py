"""SchedulingWriteAdapter protocol and NullSchedulingWriteAdapter.

The SchedulingWriteAdapter is the interface for providers that execute
scheduling write commands (schedule, cancel, reschedule). It is separate
from the read-only SchedulingAdapter (check_availability).

The OutboxDrainerWorker routes scheduling commands by ``provider`` field:
  "google_calendar" → GoogleCalendarWriteAdapter
  "clinicorp"       → ClinicorpWriteAdapter
  "chatwoot"        → ChatwootMcpAdapter
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from iara.contracts.errors import FailClosedError
from iara.contracts.provider import ProviderCommand, ProviderMutationResult, ProviderSecurityContext
from iara.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class SchedulingWriteAdapter(Protocol):
    """Protocol for providers that execute scheduling write commands.

    Implementations must be idempotent — the outbox drainer may retry on
    transient failures. All methods must validate tenant_id cross-tenant
    before writing (INV-02).
    """

    @property
    def provider_name(self) -> str:
        """Return the provider name string."""
        ...

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a scheduling write command and return a result ref.

        Args:
            command: The provider command to execute.
            security_context: Verified security context.

        Returns:
            ProviderMutationResult: Sanitized execution result.

        Raises:
            FailClosedError: On cross-tenant mismatch or unregistered capability.
            ProviderError: On provider API failure after exhausting retries.
        """
        ...

    async def health_check(self) -> bool:
        """Return True if the provider API is reachable."""
        ...


class NullSchedulingWriteAdapter:
    """No-op scheduling write adapter for when no provider is configured.

    All commands are logged as skipped and return a null result ref.
    Used in development and when the provider credential is absent.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "null"

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Log the command as skipped and return a null result.

        Args:
            command: The provider command (not executed).
            security_context: Security context (cross-tenant check still runs).

        Returns:
            ProviderMutationResult: Null result indicating no-op.
        """
        if str(command.tenant_id) != str(security_context.tenant_id):
            raise FailClosedError(
                f"Scheduling command tenant {command.tenant_id} does not match"
                f" security context {security_context.tenant_id}"
            )
        logger.warning(
            "scheduling_command_skipped_no_provider",
            capability_name=command.capability_name,
            command_id=str(command.command_id),
            provider_name=self.provider_name,
        )
        return ProviderMutationResult(
            command_id=str(command.command_id),
            idempotency_key=command.idempotency_key,
            success=True,
            readback_confirmed=False,
            result_ref=f"skipped:{command.command_id}",
        )

    async def health_check(self) -> bool:
        """Always return True (null adapter is always healthy)."""
        return True
