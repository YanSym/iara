"""Unit tests for Agent Tools — registry, policy guard, and gateway."""

from __future__ import annotations

import uuid

import pytest

from iara.contracts.tools import AgentToolDefinition, ToolStatus
from iara.tools.policy_guard import OperationMode, ToolPolicyGuard
from iara.tools.registry import AgentToolRegistry


@pytest.mark.unit
class TestAgentToolRegistry:
    """Tests for AgentToolRegistry."""

    def test_active_tools_visible_to_agent(self) -> None:
        """Only ACTIVE tools must appear in agent tool list."""
        registry = AgentToolRegistry.build_default(tenant_id=str(uuid.uuid4()))
        active_tools = registry.get_active_tools()
        assert len(active_tools) > 0
        for tool in active_tools:
            assert tool.status == ToolStatus.ACTIVE

    def test_inactive_tool_not_visible(self) -> None:
        """Inactive tools must not appear in the agent tool list."""
        tenant_id = str(uuid.uuid4())
        registry = AgentToolRegistry(tenant_id=tenant_id)
        registry.register(
            AgentToolDefinition(
                tool_name="test_inactive",
                display_name="Inactive Tool",
                description="Should not be visible",
                status=ToolStatus.INACTIVE,
            )
        )
        active_tools = registry.get_active_tools()
        tool_names = [t.tool_name for t in active_tools]
        assert "test_inactive" not in tool_names

    def test_draft_tool_not_visible(self) -> None:
        """Draft tools must not appear in the agent tool list."""
        tenant_id = str(uuid.uuid4())
        registry = AgentToolRegistry(tenant_id=tenant_id)
        registry.register(
            AgentToolDefinition(
                tool_name="test_draft",
                display_name="Draft Tool",
                description="Not published",
                status=ToolStatus.DRAFT,
            )
        )
        tool_names = registry.get_tool_names_for_prompt()
        assert "test_draft" not in tool_names

    def test_is_active_returns_correct_status(self) -> None:
        """is_active() must return True only for ACTIVE tools."""
        tenant_id = str(uuid.uuid4())
        registry = AgentToolRegistry(tenant_id=tenant_id)
        registry.register(
            AgentToolDefinition(
                tool_name="active_tool",
                display_name="Active",
                description="Active",
                status=ToolStatus.ACTIVE,
            )
        )
        assert registry.is_active("active_tool") is True
        assert registry.is_active("nonexistent_tool") is False


@pytest.mark.unit
class TestToolPolicyGuard:
    """Tests for ToolPolicyGuard."""

    def test_read_only_tool_always_allowed(self) -> None:
        """Read-only tools must always be allowed."""
        guard = ToolPolicyGuard(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="availability",
            display_name="Check Availability",
            description="Read-only",
            status=ToolStatus.ACTIVE,
            is_side_effecting=False,
        )
        result = guard.check(tool, {})
        assert result.approved is True
        assert result.mode == OperationMode.READ_ONLY

    def test_kanban_suggest_only_mode(self) -> None:
        """Kanban tools must produce suggestions in suggest_only mode."""
        guard = ToolPolicyGuard(
            tenant_id=str(uuid.uuid4()),
            kanban_mode=OperationMode.SUGGEST_ONLY,
        )
        tool = AgentToolDefinition(
            tool_name="kanban_update_status",
            display_name="Update Kanban",
            description="Write kanban",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        result = guard.check(tool, {"stage": "qualified"})
        assert result.approved is True
        assert result.mode == OperationMode.SUGGEST_ONLY
        assert result.produces_draft is True

    def test_campaign_dispatch_blocked_in_draft_only(self) -> None:
        """Campaign dispatch must be blocked in draft_only mode."""
        guard = ToolPolicyGuard(
            tenant_id=str(uuid.uuid4()),
            campaign_mode=OperationMode.DRAFT_ONLY,
        )
        tool = AgentToolDefinition(
            tool_name="campaign_dispatch_batch",
            display_name="Dispatch Campaign",
            description="High risk send",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        result = guard.check(tool, {"campaign_run_ref": "run_001"})
        assert result.approved is False

    def test_inactive_tool_raises_fail_closed(self) -> None:
        """Inactive tool must raise FailClosedError."""
        from iara.contracts.errors import FailClosedError

        guard = ToolPolicyGuard(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="inactive_tool",
            display_name="Inactive",
            description="Not active",
            status=ToolStatus.INACTIVE,
            is_side_effecting=True,
        )
        with pytest.raises(FailClosedError):
            guard.check(tool, {})
