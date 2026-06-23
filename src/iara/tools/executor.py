"""ToolExecutor — executes Agent Tools after policy validation.

Side-effecting tools emit ProviderCommands to the outbox. They never
execute provider calls directly inside this executor (INV-04).

Read-only tools delegate to the corresponding catalog module handlers.
Follow-up scheduling routes to follow_up_queue (not the outbox) so the
follow-up scheduler worker sends the message at trigger_at.
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

# Capabilities that route to the follow-up queue instead of the outbox.
# These tools schedule deferred messages; the FollowUpSchedulerWorker sends them
# at trigger_at via the outbox. Direct outbox dispatch is intentionally bypassed.
_FOLLOW_UP_CAPABILITIES: frozenset[str] = frozenset({"followup_reengage_conversation"})


class ToolExecutor:
    """Executes Agent Tools in the correct mode with outbox side-effects.

    Args:
        outbox_service: Service to enqueue provider commands.
        tenant_id: Tenant UUID string.
        scheduling_adapter: Read-only scheduling provider (optional).
        follow_up_repo: Follow-up queue repository (optional).
    """

    def __init__(
        self,
        tenant_id: str,
        outbox_service: Any | None = None,
        scheduling_adapter: Any | None = None,
        follow_up_repo: Any | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._outbox = outbox_service
        self._scheduling_adapter = scheduling_adapter
        self._follow_up_repo = follow_up_repo

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
        # Follow-up scheduling routes to follow_up_queue
        if request.tool_name in _FOLLOW_UP_CAPABILITIES:
            return await self._execute_follow_up(request)

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
        """Execute a read-only tool by delegating to its catalog handler.

        Args:
            request: The invocation request.
            tool: The tool definition.

        Returns:
            ToolInvocationResult: Read result.
        """
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
            "arguments_received": list(request.arguments.keys()),
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

    async def _execute_follow_up(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """Build a follow-up payload and enqueue it to follow_up_queue.

        The follow-up scheduler worker handles actual message delivery at
        trigger_at. This path never writes to the outbox (INV-04 compliant —
        the queue write is deterministic and idempotent).

        Args:
            request: The invocation request.

        Returns:
            ToolInvocationResult: Scheduled or skipped result.
        """
        from iara.tools.catalog.followup import build_followup_schedule_payload

        payload = build_followup_schedule_payload(
            arguments=request.arguments,
            tenant_id=self._tenant_id,
            conversation_id=request.arguments.get("conversation_id", ""),
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
        )

        if payload.get("status") == "skipped":
            return ToolInvocationResult(
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                status=ToolResultStatus.SUCCESS,
                result_summary="Follow-up skipped (opted-out or quiet hours).",
                result_data=payload,
                call_id=request.call_id,
            )

        if self._follow_up_repo is not None:
            try:
                await self._follow_up_repo.enqueue_raw(payload)
            except Exception as exc:
                logger.warning(
                    "follow_up_enqueue_failed",
                    error_code=type(exc).__name__,
                    idempotency_key=request.idempotency_key,
                )

        return ToolInvocationResult(
            invocation_id=request.invocation_id,
            tool_name=request.tool_name,
            status=ToolResultStatus.SUCCESS,
            result_summary=f"Follow-up scheduled for {payload.get('trigger_at', 'unknown time')}.",
            result_data={
                "trigger_at": payload.get("trigger_at", ""),
                "message_ref": payload.get("message_ref", ""),
                "status": "scheduled",
            },
            call_id=request.call_id,
        )

    async def _dispatch_read_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Route read-only tool calls to the catalog module handler.

        Each catalog handler returns sanitized data — no PII, no raw provider
        responses. When a handler is not registered, returns a structured error
        so the agent can handle it gracefully instead of crashing.

        Args:
            tool_name: The logical tool name.
            arguments: Tool arguments.

        Returns:
            dict[str, Any]: Sanitized read result.
        """
        match tool_name:
            case "availability":
                from iara.tools.catalog import scheduling

                if self._scheduling_adapter is not None:
                    scheduling._SCHEDULING_ADAPTER = self._scheduling_adapter
                return await scheduling.handle_availability(arguments)

            case "kanban_analyze_conversation":
                from iara.tools.catalog import kanban

                return await kanban.handle_kanban_analyze(arguments)

            case "lead_search":
                from iara.tools.catalog import lead

                return await lead.handle_lead_search(arguments)

            case "history_analyze_conversations":
                from iara.tools.catalog import history

                return await history.handle_history_analyze(arguments)

            case "campaign_status":
                from iara.tools.catalog import campaigns

                return await campaigns.handle_campaign_status(arguments)

            case "campaign_validate_audience":
                from iara.tools.catalog import campaigns

                return await campaigns.handle_campaign_validate_audience(arguments)

            case "kb_suggest":
                from iara.tools.catalog import kb

                return await kb.handle_kb_suggest(arguments)

            case _:
                logger.warning(
                    "read_tool_not_registered",
                    tool_name=tool_name,
                )
                return {
                    "status": "not_implemented",
                    "tool_name": tool_name,
                    "message": "This read capability has no registered handler.",
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
