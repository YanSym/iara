"""ToolExecutor — executes Agent Tools after policy validation.

Side-effecting tools emit ProviderCommands to the outbox. They never
execute provider calls directly inside this executor (INV-04).
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.contracts.tools import (
    AgentToolDefinition,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolResultStatus,
)
from iara.observability.logging import get_logger
from iara.tools.policy_guard import OperationMode, PolicyCheckResult

logger = get_logger(__name__)


class ToolExecutor:
    """Executes Agent Tools in the correct mode with outbox side-effects.

    Args:
        outbox_service: Service to enqueue provider commands.
        tenant_id: Tenant UUID string.
    """

    def __init__(
        self,
        tenant_id: str,
        outbox_service: Any | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._outbox = outbox_service

    async def execute(
        self,
        request: ToolInvocationRequest,
        tool: AgentToolDefinition,
        policy_result: PolicyCheckResult,
    ) -> ToolInvocationResult:
        """Execute a tool invocation.

        Routes to the appropriate execution strategy based on the policy result.

        Args:
            request: The tool invocation request.
            tool: The tool definition.
            policy_result: The policy check result.

        Returns:
            ToolInvocationResult: Sanitized invocation result.
        """
        # Read-only tools
        if not tool.is_side_effecting:
            return await self._execute_read(request, tool)

        # Draft mode — produce a draft, no real side effects
        if policy_result.produces_draft or policy_result.mode in (
            OperationMode.DRAFT_ONLY,
            OperationMode.SUGGEST_ONLY,
            OperationMode.DRY_RUN,
        ):
            return await self._execute_draft(request, tool, policy_result)

        # Real execution — emit to outbox
        return await self._execute_with_outbox(request, tool, policy_result)

    async def _execute_read(
        self,
        request: ToolInvocationRequest,
        tool: AgentToolDefinition,
    ) -> ToolInvocationResult:
        """Execute a read-only tool.

        Args:
            request: The invocation request.
            tool: The tool definition.

        Returns:
            ToolInvocationResult: Read result.
        """
        # Route to specific tool handlers
        result_data = await self._dispatch_read_tool(request.tool_name, request.arguments)
        return ToolInvocationResult(
            invocation_id=request.invocation_id,
            tool_name=request.tool_name,
            status=ToolResultStatus.SUCCESS,
            result_summary=self._summarize(request.tool_name, result_data),
            result_data=result_data,
            call_id=request.call_id,
        )

    async def _execute_draft(
        self,
        request: ToolInvocationRequest,
        tool: AgentToolDefinition,
        policy_result: PolicyCheckResult,
    ) -> ToolInvocationResult:
        """Execute a tool in draft mode — no real side effects.

        Args:
            request: The invocation request.
            tool: The tool definition.
            policy_result: Policy result.

        Returns:
            ToolInvocationResult: Draft result.
        """
        draft_ref = f"draft:{str(uuid.uuid4())[:8]}"
        result_data = {
            "draft_ref": draft_ref,
            "mode": policy_result.mode,
            "tool_name": request.tool_name,
            "arguments_received": list(request.arguments.keys()),  # Keys only, not values
        }
        return ToolInvocationResult(
            invocation_id=request.invocation_id,
            tool_name=request.tool_name,
            status=ToolResultStatus.DRAFT_CREATED,
            result_summary=f"Draft created for {request.tool_name!r} (mode={policy_result.mode}). "
            f"Human review required before execution.",
            result_data=result_data,
            draft_ref=draft_ref,
            call_id=request.call_id,
        )

    async def _execute_with_outbox(
        self,
        request: ToolInvocationRequest,
        tool: AgentToolDefinition,
        policy_result: PolicyCheckResult,
    ) -> ToolInvocationResult:
        """Execute a tool by emitting a command to the outbox.

        Per INV-04: side effects never execute directly in a replayable node.

        Args:
            request: The invocation request.
            tool: The tool definition.
            policy_result: Policy result.

        Returns:
            ToolInvocationResult: Result with outbox command reference.
        """
        command_id = str(uuid.uuid4())
        outbox_ref = None

        if self._outbox is not None:
            # Emit to outbox — actual execution happens asynchronously
            # by the outbox drainer worker
            await self._outbox.enqueue_tool_command(
                command_id=command_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                tenant_id=self._tenant_id,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
            )
            outbox_ref = command_id

        return ToolInvocationResult(
            invocation_id=request.invocation_id,
            tool_name=request.tool_name,
            status=ToolResultStatus.SUCCESS,
            result_summary=f"Tool {request.tool_name!r} queued for execution.",
            result_data={"command_id": command_id, "mode": policy_result.mode},
            outbox_command_id=outbox_ref,
            call_id=request.call_id,
        )

    async def _dispatch_read_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a read-only tool call to the appropriate handler.

        Args:
            tool_name: The logical tool name.
            arguments: Tool arguments.

        Returns:
            dict[str, Any]: Sanitized read result.
        """
        # Read-only tool handlers (stubs for now — real implementations in catalog/)
        handlers: dict[str, Any] = {
            "availability": self._handle_availability,
            "lead_search": self._handle_lead_search,
            "kanban_analyze_conversation": self._handle_kanban_analyze,
            "history_analyze_conversations": self._handle_history_analyze,
            "campaign_status": self._handle_campaign_status,
            "campaign_validate_audience": self._handle_campaign_validate,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return {"status": "not_implemented", "tool_name": tool_name}
        result: dict[str, Any] = await handler(arguments)
        return result

    async def _handle_availability(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub availability data."""
        return {
            "available_slots": 3,
            "next_available": "2026-06-10T09:00:00",
            "note": "Availability check — stub implementation",
        }

    async def _handle_lead_search(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub lead search results."""
        return {
            "results_count": 0,
            "note": "Lead search — stub implementation",
        }

    async def _handle_kanban_analyze(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub kanban analysis."""
        return {
            "suggested_stage": "nurturing",
            "confidence": 0.75,
            "mode": "suggest_only",
        }

    async def _handle_history_analyze(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub history analysis."""
        return {
            "analyzed_count": 0,
            "draft_ref": f"draft:{str(uuid.uuid4())[:8]}",
            "note": "History analysis — stub implementation",
        }

    async def _handle_campaign_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub campaign status."""
        return {
            "status": "draft",
            "sent_count": 0,
            "failed_count": 0,
        }

    async def _handle_campaign_validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return stub campaign validation."""
        return {
            "eligible_count": 0,
            "opted_out_count": 0,
            "note": "Audience validation — stub implementation",
        }

    def _summarize(self, tool_name: str, result_data: dict[str, Any]) -> str:
        """Create a brief summary for the agent's context.

        Args:
            tool_name: The tool name.
            result_data: The result data.

        Returns:
            str: One-sentence summary.
        """
        return f"Tool {tool_name!r} completed successfully with {len(result_data)} result fields."
