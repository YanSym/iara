"""Unit tests for ToolExecutor — dispatch logic and draft/outbox routing.

No real LLM, no real database, no network calls.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from iara.contracts.tools import (
    AgentToolDefinition,
    ToolInvocationRequest,
    ToolResultStatus,
    ToolStatus,
)
from iara.tools.executor import ToolExecutor
from iara.tools.policy_guard import OperationMode, PolicyCheckResult


def _make_request(tool_name: str, tenant_id: str | None = None) -> ToolInvocationRequest:
    """Create a minimal tool invocation request."""
    return ToolInvocationRequest(
        invocation_id=str(uuid.uuid4()),
        tool_name=tool_name,
        arguments={},
        tenant_id=uuid.UUID(tenant_id) if tenant_id else uuid.uuid4(),
        conversation_id="conv_test_001",
        idempotency_key=f"idem:{uuid.uuid4()}",
        correlation_id=str(uuid.uuid4()),
        call_id=f"call_{uuid.uuid4().hex[:8]}",
    )


def _policy(
    mode: OperationMode, approved: bool = True, produces_draft: bool = False
) -> PolicyCheckResult:
    """Create a minimal policy check result."""
    return PolicyCheckResult(approved=approved, mode=mode, produces_draft=produces_draft)


@pytest.mark.unit
class TestToolExecutorReadOnly:
    """Tests for read-only tool execution path."""

    @pytest.mark.asyncio
    async def test_read_tool_returns_success_status(self) -> None:
        """Read-only tool must return SUCCESS status."""
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="availability",
            display_name="Check Availability",
            description="Read-only",
            status=ToolStatus.ACTIVE,
            is_side_effecting=False,
        )
        request = _make_request("availability")
        policy = _policy(OperationMode.READ_ONLY)

        result = await executor.execute(request, tool, policy)

        assert result.status == ToolResultStatus.SUCCESS
        assert result.tool_name == "availability"

    @pytest.mark.asyncio
    async def test_unknown_read_tool_returns_not_implemented(self) -> None:
        """Unknown read tool must return a not_implemented result dict."""
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="unknown_read_tool",
            display_name="Unknown",
            description="Unknown",
            status=ToolStatus.ACTIVE,
            is_side_effecting=False,
        )
        request = _make_request("unknown_read_tool")
        policy = _policy(OperationMode.READ_ONLY)

        result = await executor.execute(request, tool, policy)

        assert result.status == ToolResultStatus.SUCCESS
        assert result.result_data is not None
        assert result.result_data.get("status") == "not_implemented"


@pytest.mark.unit
class TestToolExecutorDraftMode:
    """Tests for draft-mode tool execution path."""

    @pytest.mark.asyncio
    async def test_draft_mode_returns_draft_created_status(self) -> None:
        """Draft mode must return DRAFT_CREATED status."""
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="schedule",
            display_name="Schedule",
            description="Write",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        request = _make_request("schedule")
        policy = _policy(OperationMode.DRAFT_ONLY, produces_draft=True)

        result = await executor.execute(request, tool, policy)

        assert result.status == ToolResultStatus.DRAFT_CREATED
        assert result.draft_ref is not None
        assert result.draft_ref.startswith("draft:")

    @pytest.mark.asyncio
    async def test_suggest_only_produces_draft(self) -> None:
        """suggest_only policy must produce a draft, not execute."""
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()))
        tool = AgentToolDefinition(
            tool_name="kanban_update_status",
            display_name="Kanban Update",
            description="Write kanban",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        request = _make_request("kanban_update_status")
        policy = _policy(OperationMode.SUGGEST_ONLY, produces_draft=True)

        result = await executor.execute(request, tool, policy)

        assert result.status == ToolResultStatus.DRAFT_CREATED


@pytest.mark.unit
class TestToolExecutorOutbox:
    """Tests for outbox-based side-effecting tool execution."""

    @pytest.mark.asyncio
    async def test_write_tool_emits_to_outbox(self) -> None:
        """Side-effecting tool must emit to outbox when outbox is configured."""
        mock_outbox = AsyncMock()
        mock_outbox.enqueue_tool_command = AsyncMock()
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()), outbox_service=mock_outbox)

        tool = AgentToolDefinition(
            tool_name="schedule",
            display_name="Schedule",
            description="Write",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        request = _make_request("schedule")
        # Not draft — real execution path
        policy = _policy(OperationMode.READ_ONLY)

        result = await executor.execute(request, tool, policy)

        mock_outbox.enqueue_tool_command.assert_called_once()
        assert result.outbox_command_id is not None

    @pytest.mark.asyncio
    async def test_write_tool_without_outbox_still_returns_success(self) -> None:
        """Side-effecting tool must return success even if no outbox is configured."""
        executor = ToolExecutor(tenant_id=str(uuid.uuid4()), outbox_service=None)
        tool = AgentToolDefinition(
            tool_name="schedule",
            display_name="Schedule",
            description="Write",
            status=ToolStatus.ACTIVE,
            is_side_effecting=True,
        )
        request = _make_request("schedule")
        policy = _policy(OperationMode.READ_ONLY)

        result = await executor.execute(request, tool, policy)

        assert result.status == ToolResultStatus.SUCCESS
        assert result.outbox_command_id is None
