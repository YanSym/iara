"""AgentToolMcpGateway — validates, resolves, and executes Agent Tools.

The gateway is the entry point for all tool invocations from the LangGraph
agent node. It:
1. Validates the tool is registered and active (registry lookup).
2. Runs the ToolPolicyGuard check.
3. Delegates to the ToolExecutor.
4. Returns a sanitized ToolInvocationResult.
"""

from __future__ import annotations

import time

from iara.contracts.errors import FailClosedError, PolicyViolationError
from iara.contracts.tools import ToolInvocationRequest, ToolInvocationResult, ToolResultStatus
from iara.observability.logging import get_logger
from iara.observability.metrics import tool_invocation_duration_seconds, tool_invocations_total
from iara.tools.executor import ToolExecutor
from iara.tools.policy_guard import ToolPolicyGuard
from iara.tools.registry import AgentToolRegistry

logger = get_logger(__name__)


class AgentToolMcpGateway:
    """Gateway for Agent Tool invocations.

    The gateway is the single entry point for tool calls from the agent.
    It ensures every invocation is registered, policy-checked, and executed
    in a controlled manner.

    Args:
        registry: The AgentToolRegistry.
        policy_guard: The ToolPolicyGuard for this tenant.
        executor: The ToolExecutor.
    """

    def __init__(
        self,
        registry: AgentToolRegistry,
        policy_guard: ToolPolicyGuard,
        executor: ToolExecutor,
    ) -> None:
        self._registry = registry
        self._policy_guard = policy_guard
        self._executor = executor

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """Invoke an Agent Tool.

        Args:
            request: The tool invocation request.

        Returns:
            ToolInvocationResult: The sanitized invocation result.
        """
        _t_start = time.monotonic()

        # 1. Registry lookup — must be active
        tool = self._registry.get_tool(request.tool_name)
        if tool is None:
            logger.warning(
                "tool_not_found",
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
            )
            tool_invocations_total.labels(tool_name=request.tool_name, status="failed").inc()
            return ToolInvocationResult(
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                status=ToolResultStatus.FAILED,
                result_summary=f"Tool {request.tool_name!r} is not registered",
                error_code="TOOL_NOT_FOUND",
                error_summary=f"Tool {request.tool_name!r} is not registered",
                call_id=request.call_id,
            )

        if not self._registry.is_active(request.tool_name):
            tool_invocations_total.labels(
                tool_name=request.tool_name, status="policy_blocked"
            ).inc()
            return ToolInvocationResult(
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                status=ToolResultStatus.POLICY_BLOCKED,
                result_summary=f"Tool {request.tool_name!r} is not active",
                error_code="TOOL_INACTIVE",
                call_id=request.call_id,
            )

        # 2. Policy check
        try:
            policy_result = self._policy_guard.check(tool, request.arguments)
        except (FailClosedError, PolicyViolationError) as exc:
            logger.warning(
                "tool_policy_blocked",
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                error_code=exc.code,
            )
            tool_invocations_total.labels(
                tool_name=request.tool_name, status="policy_blocked"
            ).inc()
            return ToolInvocationResult(
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                status=ToolResultStatus.POLICY_BLOCKED,
                result_summary=f"Tool blocked by policy: {exc.code}",
                error_code=exc.code,
                error_summary=exc.message,
                call_id=request.call_id,
            )

        if not policy_result.approved:
            tool_invocations_total.labels(
                tool_name=request.tool_name, status="policy_blocked"
            ).inc()
            return ToolInvocationResult(
                invocation_id=request.invocation_id,
                tool_name=request.tool_name,
                status=ToolResultStatus.POLICY_BLOCKED,
                result_summary=f"Tool blocked by policy: {policy_result.reason}",
                error_code="POLICY_BLOCKED",
                error_summary=policy_result.reason,
                call_id=request.call_id,
            )

        # 3. Execute
        logger.info(
            "tool_invoking",
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            produces_draft=policy_result.produces_draft,
            mode=policy_result.mode,
        )

        result = await self._executor.execute(
            request=request,
            tool=tool,
            policy_result=policy_result,
        )

        _elapsed = time.monotonic() - _t_start
        _metric_status = (
            result.status.value.lower()
            if hasattr(result.status, "value")
            else str(result.status).lower()
        )
        tool_invocations_total.labels(tool_name=request.tool_name, status=_metric_status).inc()
        tool_invocation_duration_seconds.labels(tool_name=request.tool_name).observe(_elapsed)

        logger.info(
            "tool_invoked",
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status=result.status,
        )
        return result
