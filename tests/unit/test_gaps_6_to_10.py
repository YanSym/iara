"""Tests for GAP 6-10 implementations.

- GAP 6: Postgres checkpointer factory
- GAP 7: GovernedMemoryStore with Postgres backing
- GAP 8: CommandAssistantSubgraph authorization
- GAP 9: Scheduling adapters
- GAP 10: ChatwootMcpAdapter retry + credential resolution
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from iara.contracts.errors import CrossTenantError
from iara.contracts.provider import ProviderCommand, ProviderSecurityContext, RiskClass
from iara.graph.builder import build_conversational_graph
from iara.graph.edges import should_continue_after_dispatch, should_continue_after_eligibility
from iara.graph.nodes.command_assistant import command_assistant_node
from iara.graph.nodes.memory_writer import memory_writer_node
from iara.memory.postgres_store import PostgresMemoryStore
from iara.provider.chatwoot.mcp_adapter import ChatwootMcpAdapter, _resolve_credential
from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry
from iara.provider.scheduling.factory import build_scheduling_adapter
from iara.provider.scheduling.google_calendar import _count_free_slots, _find_next_free_start
from iara.provider.scheduling.null_adapter import NullSchedulingAdapter
from iara.security.command_auth import CommandAuthorizationGuard, CommandRequesterBinding

# ── GAP 7: GovernedMemoryStore ────────────────────────────────────────────────


@pytest.mark.unit
class TestPostgresMemoryStore:
    """Tests for PostgresMemoryStore."""

    def _make_store(self, enabled: bool = True) -> Any:
        session_factory = MagicMock()
        return PostgresMemoryStore(
            session_factory=session_factory,
            tenant_id=str(uuid.uuid4()),
            enabled=enabled,
        )

    @pytest.mark.asyncio
    async def test_store_disabled_returns_false(self) -> None:
        """Disabled store should return False without touching the DB."""
        store = self._make_store(enabled=False)
        result = await store.store(key="k1", content="some fact")
        assert result is False

    @pytest.mark.asyncio
    async def test_retrieve_disabled_returns_none(self) -> None:
        """Disabled store should return None for any key."""
        store = self._make_store(enabled=False)
        result = await store.retrieve(key="k1")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_recent_disabled_returns_empty(self) -> None:
        """Disabled store should return an empty list."""
        store = self._make_store(enabled=False)
        result = await store.list_recent()
        assert result == []

    @pytest.mark.asyncio
    async def test_store_enabled_calls_session(self) -> None:
        """Enabled store should open a DB session and commit."""
        store = self._make_store(enabled=True)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        store._session_factory = MagicMock(return_value=mock_session)

        result = await store.store(key="k1", content="important fact")
        assert result is True
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_purge_calls_delete(self) -> None:
        """Purge should execute a DELETE statement and commit."""
        store = self._make_store(enabled=True)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        store._session_factory = MagicMock(return_value=mock_session)

        deleted = await store.purge(key="k1")
        assert deleted is True
        mock_session.commit.assert_called_once()


# ── GAP 8: CommandAuthorizationGuard ─────────────────────────────────────────


@pytest.mark.unit
class TestCommandAuthorizationGuard:
    """Tests for CommandAuthorizationGuard and CommandRequesterBinding."""

    def _make_guard(self) -> Any:
        guard = CommandAuthorizationGuard()
        guard.register(CommandRequesterBinding(tenant_id="tenant_abc"))
        return guard

    def test_regular_contact_message_not_admin(self) -> None:
        """Contact messages without admin prefix should not be flagged."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="contact",
            sender_ref="123",
            message_content="Olá, quero agendar uma consulta",
        )
        assert result.is_admin_command is False
        assert result.allowed is True

    def test_admin_prefix_from_authorized_agent(self) -> None:
        """Agent sender with /iara prefix should be authorized."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="agent",
            sender_ref="agent_007",
            message_content="/iara list_campaigns",
        )
        assert result.is_admin_command is True
        assert result.allowed is True

    def test_admin_prefix_from_unauthorized_contact(self) -> None:
        """Contact sending /iara command should be denied."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="contact",
            sender_ref="456",
            message_content="/iara enable_campaign 123",
        )
        assert result.is_admin_command is True
        assert result.allowed is False
        assert result.reason == "unauthorized_sender"

    def test_admin_bot_is_authorized_by_default(self) -> None:
        """agent_bot sender type should be authorized by default."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="agent_bot",
            sender_ref="bot_v1",
            message_content="/admin status check now",
        )
        assert result.is_admin_command is True
        assert result.allowed is True

    def test_short_admin_message_not_detected(self) -> None:
        """Messages that are too short to contain a command should be ignored."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="agent",
            sender_ref="agent_1",
            message_content="/iara",  # only 5 chars — below min length
        )
        assert result.is_admin_command is False

    def test_at_iara_prefix_works(self) -> None:
        """@iara prefix should also trigger admin detection."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="tenant_abc",
            sender_type="agent_bot",
            sender_ref="bot",
            message_content="@iara get current config",
        )
        assert result.is_admin_command is True
        assert result.allowed is True

    def test_unknown_tenant_uses_default_binding(self) -> None:
        """Unknown tenant should use default binding (agents authorized)."""
        guard = self._make_guard()
        result = guard.check(
            tenant_id="unknown_tenant",
            sender_type="agent",
            sender_ref="agent_x",
            message_content="/iara check system status",
        )
        assert result.is_admin_command is True
        assert result.allowed is True


# ── GAP 8: CommandAssistantNode ───────────────────────────────────────────────


@pytest.mark.unit
class TestCommandAssistantNode:
    """Tests for command_assistant_node parsing and dispatch."""

    @pytest.mark.asyncio
    async def test_help_command_lists_commands(self) -> None:
        """help command should list all available admin commands."""
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": "/iara help please"}],
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "conv_1",
            "step_count": 1,
        }
        result = await command_assistant_node(state)
        assert "agent_response" in result
        assert (
            "help" in result["agent_response"].lower()
            or "comando" in result["agent_response"].lower()
        )
        assert result.get("response_sent") is True

    @pytest.mark.asyncio
    async def test_status_command_returns_system_info(self) -> None:
        """status command should return system status information."""
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": "/iara status check"}],
            "tenant_id": "tenant_xyz",
            "conversation_id": "conv_2",
            "step_count": 0,
        }
        result = await command_assistant_node(state)
        assert "status" in result["agent_response"].lower() or "IAra" in result["agent_response"]

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error(self) -> None:
        """Unknown command should return a helpful error message."""
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": "/admin foobar_unknown"}],
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "conv_3",
            "step_count": 0,
        }
        result = await command_assistant_node(state)
        assert (
            "foobar_unknown" in result["agent_response"]
            or "não reconhecido" in result["agent_response"]
        )

    @pytest.mark.asyncio
    async def test_step_count_incremented(self) -> None:
        """Node must increment step_count."""
        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": "/iara help commands"}],
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "c",
            "step_count": 5,
        }
        result = await command_assistant_node(state)
        assert result["step_count"] == 6


# ── GAP 9: Scheduling adapters ────────────────────────────────────────────────


@pytest.mark.unit
class TestNullSchedulingAdapter:
    """Tests for NullSchedulingAdapter."""

    @pytest.mark.asyncio
    async def test_returns_zero_slots(self) -> None:
        """Null adapter should always return zero available slots."""
        adapter = NullSchedulingAdapter()
        result = await adapter.check_availability(
            tenant_id="t1",
            date_range_start="2026-06-10T09:00:00+00:00",
            date_range_end="2026-06-10T17:00:00+00:00",
        )
        assert result["available_slots_count"] == 0
        assert result["next_available_slot"] is None
        assert result["configured"] is False
        assert result["provider"] == "null"

    def test_is_not_configured(self) -> None:
        """Null adapter should report itself as not configured."""
        adapter = NullSchedulingAdapter()
        assert adapter.is_configured is False


@pytest.mark.unit
class TestSchedulingAdapterFactory:
    """Tests for build_scheduling_adapter factory."""

    def _make_settings(self, *, clinicorp: bool = False, google: bool = False) -> Any:
        settings = MagicMock()
        settings.clinicorp_enabled = clinicorp
        settings.clinicorp_base_url = "https://api.clinicorp.com"
        settings.clinicorp_credential_ref = "secret://clinicorp/api_key"
        settings.google_calendar_enabled = google
        settings.google_calendar_credential_ref = "secret://google/service_account_json"
        return settings

    def test_null_when_neither_configured(self) -> None:
        """Should return NullSchedulingAdapter when nothing is enabled."""
        settings = self._make_settings()
        adapter = build_scheduling_adapter(settings)
        assert isinstance(adapter, NullSchedulingAdapter)

    def test_clinicorp_unresolved_falls_back_to_null(self) -> None:
        """When Clinicorp enabled but credential unresolved, use null."""
        settings = self._make_settings(clinicorp=True)
        # credential_ref starts with secret:// and there's no matching env var
        adapter = build_scheduling_adapter(settings)
        assert isinstance(adapter, NullSchedulingAdapter)


@pytest.mark.unit
class TestGoogleCalendarSlotCounting:
    """Tests for the free-slot counting helpers."""

    def test_all_busy_returns_zero(self) -> None:
        """When entire range is busy, should return zero slots."""
        busy = [{"start": "2026-06-10T09:00:00+00:00", "end": "2026-06-10T17:00:00+00:00"}]
        count = _count_free_slots(
            "2026-06-10T09:00:00+00:00",
            "2026-06-10T17:00:00+00:00",
            busy,
        )
        assert count == 0

    def test_no_busy_returns_max_slots(self) -> None:
        """Empty busy list should return all 30-min slots in the range."""
        # 8-hour range = 16 slots of 30 min
        count = _count_free_slots(
            "2026-06-10T09:00:00+00:00",
            "2026-06-10T17:00:00+00:00",
            [],
        )
        assert count == 16

    def test_partial_busy_reduces_count(self) -> None:
        """One 2-hour busy period should reduce count by 4 slots."""
        busy = [{"start": "2026-06-10T10:00:00+00:00", "end": "2026-06-10T12:00:00+00:00"}]
        count = _count_free_slots(
            "2026-06-10T09:00:00+00:00",
            "2026-06-10T17:00:00+00:00",
            busy,
        )
        assert count == 12

    def test_find_next_free_start_before_busy(self) -> None:
        """Should return range start when it precedes all busy periods."""
        busy = [{"start": "2026-06-10T10:00:00+00:00", "end": "2026-06-10T11:00:00+00:00"}]
        result = _find_next_free_start("2026-06-10T09:00:00+00:00", busy)
        assert result is not None
        assert "09:00" in result

    def test_find_next_free_start_after_busy(self) -> None:
        """Should skip the busy period and return after it."""
        busy = [{"start": "2026-06-10T09:00:00+00:00", "end": "2026-06-10T10:00:00+00:00"}]
        result = _find_next_free_start("2026-06-10T09:00:00+00:00", busy)
        assert result is not None
        assert "10:00" in result


# ── GAP 10: ChatwootMcpAdapter ────────────────────────────────────────────────


@pytest.mark.unit
class TestCredentialResolution:
    """Tests for _resolve_credential helper."""

    def test_plain_value_returned_as_is(self) -> None:
        """Non-secret-ref values should be returned unchanged."""
        assert _resolve_credential("my_direct_token") == "my_direct_token"

    def test_env_var_looked_up_for_secret_ref(self, monkeypatch: Any) -> None:
        """secret:// refs should be resolved via env var lookup."""
        monkeypatch.setenv("CHATWOOT_MCP_TOKEN", "real_token_from_env")
        result = _resolve_credential("secret://chatwoot/mcp_token")
        assert result == "real_token_from_env"

    def test_env_var_fallback_to_ref_when_unset(self, monkeypatch: Any) -> None:
        """When env var is missing, ref should be returned (logs warning)."""
        monkeypatch.delenv("CHATWOOT_MCP_TOKEN", raising=False)
        monkeypatch.delenv("CHATWOOT_API_TOKEN", raising=False)
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        monkeypatch.delenv("CHATWOOT_MCP_TOKEN_2", raising=False)

        result = _resolve_credential("secret://chatwoot/mcp_token")
        assert result == "secret://chatwoot/mcp_token"


@pytest.mark.unit
class TestChatwootMcpAdapterRetry:
    """Tests for ChatwootMcpAdapter retry behaviour."""

    # MCP endpoint: {base_url}/mcp/{account_id}/{slug}
    _BASE = "http://localhost:3030"
    _ACCOUNT = "59"
    _SLUG = "mcp-suporte"
    _MCP_PATH = f"/mcp/{_ACCOUNT}/{_SLUG}"

    def _make_adapter(self, max_retries: int = 3) -> Any:
        registry = MagicMock(spec=ChatwootMcpRegistry)
        resolution = MagicMock()
        resolution.allowed = True
        resolution.resolved_tool_name = "conversation_message_send"
        resolution.requires_readback = False
        registry.resolve_intent.return_value = resolution

        return ChatwootMcpAdapter(
            registry=registry,
            mcp_base_url=self._BASE,
            account_id=self._ACCOUNT,
            mcp_slug=self._SLUG,
            credential_ref="test_token",
            max_retries=max_retries,
        )

    @pytest.mark.asyncio
    async def test_successful_command_returns_result(self) -> None:
        """A 200 response should return a ProviderMutationResult."""
        adapter = self._make_adapter()
        tenant_id = uuid.uuid4()

        command = ProviderCommand(
            command_id="cmd_001",
            idempotency_key="idem_001",
            tenant_id=tenant_id,
            provider="chatwoot",
            account_id_ref="",
            capability_name="send_message",
            parameters={"conversation_id": "123", "content": "hi"},
            risk_class=RiskClass.LOW_WRITE,
            correlation_id="corr_001",
        )
        security_ctx = ProviderSecurityContext(
            tenant_id=tenant_id,
            provider="chatwoot",
            account_id_ref="",
            inbox_id="",
            capability_name="send_message",
            risk_class=RiskClass.LOW_WRITE,
        )

        with respx.mock(base_url=self._BASE) as mock:
            mock.post(self._MCP_PATH).mock(
                return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
            )
            result = await adapter.execute_command(command, security_ctx)

        assert result.success is True
        assert result.command_id == "cmd_001"
        assert result.result_ref  # non-empty hash

    @pytest.mark.asyncio
    async def test_cross_tenant_error_raised_immediately(self) -> None:
        """Cross-tenant mismatch should raise CrossTenantError without retrying."""
        adapter = self._make_adapter()
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()

        command = ProviderCommand(
            command_id="cmd_002",
            idempotency_key="idem_002",
            tenant_id=tenant_a,
            provider="chatwoot",
            account_id_ref="",
            capability_name="send_message",
            parameters={},
            risk_class=RiskClass.LOW_WRITE,
            correlation_id="corr_002",
        )
        security_ctx = ProviderSecurityContext(
            tenant_id=tenant_b,  # mismatch!
            provider="chatwoot",
            account_id_ref="",
            inbox_id="",
            capability_name="send_message",
            risk_class=RiskClass.LOW_WRITE,
        )

        with pytest.raises(CrossTenantError):
            await adapter.execute_command(command, security_ctx)

    @pytest.mark.asyncio
    async def test_network_error_retried(self) -> None:
        """Transient network errors should be retried up to max_retries."""
        adapter = self._make_adapter(max_retries=2)

        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(503, json={"error": "service unavailable"})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        with (
            patch("asyncio.sleep", return_value=None),
            respx.mock(base_url=self._BASE) as mock,
        ):
            mock.post(self._MCP_PATH).mock(side_effect=_handler)
            result_ref = await adapter._post_with_retry("conversation_message_send", {})

        assert result_ref  # non-empty
        assert call_count == 2  # retried once after the 503


# ── GAP 7: Memory writer node ─────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryWriterNode:
    """Tests for memory_writer_node."""

    @pytest.mark.asyncio
    async def test_no_op_when_store_is_none(self) -> None:
        """Should return only step_count when no memory_store."""
        state: dict[str, Any] = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "agent_response": "hello",
            "step_count": 3,
        }
        result = await memory_writer_node(state, memory_store=None, llm=None)
        assert result == {"step_count": 4}

    @pytest.mark.asyncio
    async def test_no_op_when_disabled(self) -> None:
        """Should be no-op when memory_store is disabled."""
        store = MagicMock(spec=PostgresMemoryStore)
        store._enabled = False

        state: dict[str, Any] = {
            "messages": [{"role": "user", "content": "test"}],
            "agent_response": "ok",
            "step_count": 1,
        }
        result = await memory_writer_node(state, memory_store=store, llm=MagicMock())
        assert result == {"step_count": 2}

    @pytest.mark.asyncio
    async def test_no_op_when_no_agent_response(self) -> None:
        """Should be no-op when there's no agent response yet."""
        store = MagicMock(spec=PostgresMemoryStore)
        store._enabled = True

        state: dict[str, Any] = {
            "messages": [],
            "agent_response": "",
            "step_count": 0,
        }
        result = await memory_writer_node(state, memory_store=store, llm=MagicMock())
        assert result == {"step_count": 1}
        # store.store should NOT have been called
        store.store.assert_not_called()


# ── Graph routing: admin command path ────────────────────────────────────────


@pytest.mark.unit
class TestGraphAdminRouting:
    """Tests for admin command routing through the graph."""

    def test_eligibility_edge_routes_admin(self) -> None:
        """should_continue_after_eligibility should route admin commands."""
        state: dict[str, Any] = {
            "eligibility_status": "accepted",
            "is_admin_command": True,
        }
        assert should_continue_after_eligibility(state) == "command_assistant"

    def test_eligibility_edge_routes_regular_to_media(self) -> None:
        """Regular messages should route to media_understanding."""
        state: dict[str, Any] = {
            "eligibility_status": "accepted",
            "is_admin_command": False,
        }
        assert should_continue_after_eligibility(state) == "media_understanding"

    def test_dispatch_edge_always_routes_to_memory_writer(self) -> None:
        """should_continue_after_dispatch should always go to memory_writer."""
        assert should_continue_after_dispatch({}) == "memory_writer"

    def test_graph_compiles_with_all_new_nodes(self) -> None:
        """Graph should compile successfully with all new nodes registered."""
        graph = build_conversational_graph()
        assert graph is not None
        # Verify graph has the new nodes in the node map
        node_keys = list(graph.nodes.keys())
        assert "command_assistant" in node_keys
        assert "memory_writer" in node_keys
