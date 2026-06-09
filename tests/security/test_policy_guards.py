"""Security tests — high-risk write gating invariant (INV-06).

Verifies that:
- Campaigns default to ``draft_only`` (no real dispatch without APPROVED_SEND).
- Kanban defaults to ``suggest_only`` (no real writes without WRITE_CONFIRMED).
- ``campaign_dispatch_batch`` is blocked in draft_only mode.
- Approved mode allows actual execution.
- Inactive tools are fail-closed (FailClosedError).
- The gateway returns POLICY_BLOCKED — never silently executes a denied operation.
"""

from __future__ import annotations

import uuid

import pytest

from iara.contracts.errors import FailClosedError
from iara.contracts.tools import AgentToolDefinition, ToolResultStatus, ToolStatus
from iara.tools.policy_guard import OperationMode, ToolPolicyGuard
from iara.tools.registry import AgentToolRegistry


def _tool(
    name: str, *, side_effecting: bool = True, status: ToolStatus = ToolStatus.ACTIVE
) -> AgentToolDefinition:
    return AgentToolDefinition(
        tool_name=name,
        display_name=name,
        description=f"Test tool: {name}",
        is_side_effecting=side_effecting,
        status=status,
    )


@pytest.mark.unit
@pytest.mark.security
class TestKanbanDefaultsSuggestOnly:
    """Kanban writes must produce suggestions, not real DB writes, by default."""

    def test_kanban_update_suggest_only_produces_draft(self) -> None:
        """kanban_update_status in default SUGGEST_ONLY mode → produces_draft=True."""
        guard = ToolPolicyGuard(tenant_id="t1")
        result = guard.check(_tool("kanban_update_status"), {})
        assert result.approved is True
        assert result.produces_draft is True
        assert result.mode == OperationMode.SUGGEST_ONLY

    def test_kanban_comment_suggest_only_produces_draft(self) -> None:
        """kanban_comment in default SUGGEST_ONLY mode → produces_draft=True."""
        guard = ToolPolicyGuard(tenant_id="t1")
        result = guard.check(_tool("kanban_comment"), {})
        assert result.approved is True
        assert result.produces_draft is True

    def test_kanban_write_confirmed_allows_real_write(self) -> None:
        """With WRITE_CONFIRMED, kanban tools may execute real writes."""
        guard = ToolPolicyGuard(
            tenant_id="t1",
            kanban_mode=OperationMode.WRITE_CONFIRMED,
        )
        result = guard.check(_tool("kanban_update_status"), {})
        assert result.approved is True
        assert result.produces_draft is False

    def test_kanban_sandbox_allowed(self) -> None:
        """With SANDBOX mode, kanban tools are allowed (for testing pipelines)."""
        guard = ToolPolicyGuard(tenant_id="t1", kanban_mode=OperationMode.SANDBOX)
        result = guard.check(_tool("kanban_update_status"), {})
        assert result.approved is True


@pytest.mark.unit
@pytest.mark.security
class TestCampaignDefaultsDraftOnly:
    """Campaign real dispatches must be blocked without APPROVED_SEND mode."""

    def test_campaign_dispatch_blocked_in_draft_only(self) -> None:
        """campaign_dispatch_batch must be denied in default draft_only mode."""
        guard = ToolPolicyGuard(tenant_id="t1")
        result = guard.check(_tool("campaign_dispatch_batch"), {})
        assert result.approved is False
        assert result.mode == OperationMode.DRAFT_ONLY

    def test_campaign_create_draft_allowed_in_draft_only(self) -> None:
        """campaign_create_draft may run in draft_only (produces a draft)."""
        guard = ToolPolicyGuard(tenant_id="t1")
        result = guard.check(_tool("campaign_create_draft"), {})
        assert result.approved is True
        assert result.produces_draft is True

    def test_campaign_dispatch_allowed_with_approved_send(self) -> None:
        """campaign_dispatch_batch is allowed when mode=APPROVED_SEND."""
        guard = ToolPolicyGuard(
            tenant_id="t1",
            campaign_mode=OperationMode.APPROVED_SEND,
        )
        result = guard.check(_tool("campaign_dispatch_batch"), {})
        assert result.approved is True
        assert result.produces_draft is False

    def test_campaign_dry_run_produces_draft(self) -> None:
        """DRY_RUN mode for campaigns → produces_draft=True."""
        guard = ToolPolicyGuard(tenant_id="t1", campaign_mode=OperationMode.DRY_RUN)
        result = guard.check(_tool("campaign_create_draft"), {})
        assert result.approved is True
        assert result.produces_draft is True


@pytest.mark.unit
@pytest.mark.security
class TestReadOnlyToolsAlwaysAllowed:
    """Non-side-effecting tools must always pass regardless of policy mode."""

    def test_read_only_tool_passes_in_any_mode(self) -> None:
        """Read-only tools are unconditionally allowed."""
        guard = ToolPolicyGuard(
            tenant_id="t1",
            kanban_mode=OperationMode.SUGGEST_ONLY,
            campaign_mode=OperationMode.DRAFT_ONLY,
        )
        result = guard.check(_tool("availability", side_effecting=False), {})
        assert result.approved is True
        assert result.mode == OperationMode.READ_ONLY

    def test_lead_search_read_only_always_passes(self) -> None:
        """lead_search (read-only) always passes."""
        guard = ToolPolicyGuard(tenant_id="t1")
        result = guard.check(_tool("lead_search", side_effecting=False), {})
        assert result.approved is True
        assert result.mode == OperationMode.READ_ONLY


@pytest.mark.unit
@pytest.mark.security
class TestInactiveToolsFailClosed:
    """Inactive tools must raise FailClosedError — per INV-01 fail-closed principle."""

    def test_inactive_tool_raises_fail_closed(self) -> None:
        """Inactive tool status raises FailClosedError from policy check."""
        guard = ToolPolicyGuard(tenant_id="t1")
        with pytest.raises(FailClosedError):
            guard.check(_tool("kanban_update_status", status=ToolStatus.INACTIVE), {})

    def test_draft_inactive_tool_raises_fail_closed(self) -> None:
        """DRAFT status tool also raises FailClosedError."""
        guard = ToolPolicyGuard(tenant_id="t1")
        with pytest.raises(FailClosedError):
            guard.check(_tool("availability", side_effecting=False, status=ToolStatus.DRAFT), {})


@pytest.mark.unit
@pytest.mark.security
class TestGatewayBlocksPolicyDenials:
    """AgentToolMcpGateway must return POLICY_BLOCKED — never execute a denied tool."""

    @pytest.mark.asyncio
    async def test_gateway_returns_policy_blocked_for_dispatch_in_draft_mode(self) -> None:
        """Gateway must return POLICY_BLOCKED for campaign_dispatch_batch in draft_only."""
        from iara.contracts.tools import ToolInvocationRequest
        from iara.tools.executor import ToolExecutor
        from iara.tools.gateway import AgentToolMcpGateway

        registry = AgentToolRegistry.build_default(tenant_id="t_gw")
        guard = ToolPolicyGuard(tenant_id="t_gw")  # default draft_only campaign
        executor = ToolExecutor(tenant_id="t_gw")
        gateway = AgentToolMcpGateway(registry=registry, policy_guard=guard, executor=executor)

        request = ToolInvocationRequest(
            invocation_id=str(uuid.uuid4()),
            tool_name="campaign_dispatch_batch",
            arguments={"campaign_id": "camp_001"},
            tenant_id=uuid.uuid4(),
            conversation_id="conv_gw",
            correlation_id="corr_gw",
            idempotency_key="idem_gw_001",
            call_id="call_001",
        )
        result = await gateway.invoke(request)
        assert result.status == ToolResultStatus.POLICY_BLOCKED

    @pytest.mark.asyncio
    async def test_gateway_allows_read_only_tool(self) -> None:
        """Gateway must allow read-only tools even in restrictive policy modes."""
        from iara.contracts.tools import ToolInvocationRequest
        from iara.tools.executor import ToolExecutor
        from iara.tools.gateway import AgentToolMcpGateway

        registry = AgentToolRegistry.build_default(tenant_id="t_gw2")
        guard = ToolPolicyGuard(tenant_id="t_gw2")
        executor = ToolExecutor(tenant_id="t_gw2")
        gateway = AgentToolMcpGateway(registry=registry, policy_guard=guard, executor=executor)

        request = ToolInvocationRequest(
            invocation_id=str(uuid.uuid4()),
            tool_name="availability",
            arguments={
                "date_range_start": "2026-06-10T00:00:00Z",
                "date_range_end": "2026-06-10T23:59:59Z",
            },  # noqa: E501
            tenant_id=uuid.uuid4(),
            conversation_id="conv_gw2",
            correlation_id="corr_gw2",
            idempotency_key="idem_gw_002",
            call_id="call_002",
        )
        result = await gateway.invoke(request)
        assert result.status == ToolResultStatus.SUCCESS
