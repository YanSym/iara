"""Security tests — MCP isolation invariant (INV-03).

Verifies that the LLM never receives the raw Chatwoot MCP catalog or any
raw MCP tool names. The model only sees logical, published Agent Tool names
with sanitized descriptions. Raw MCP names (e.g. ``chatwoot_send_message``)
are exclusively internal to the CapabilityRegistry and never reach the LLM.
"""

from __future__ import annotations

import pytest

from iara.graph.nodes.context_builder import _build_lc_tool_definitions
from iara.provider.chatwoot.mcp_registry import DEFAULT_CHATWOOT_CAPABILITIES, ChatwootMcpRegistry
from iara.tools.registry import AgentToolRegistry


@pytest.mark.unit
@pytest.mark.security
class TestAgentToolsNeverExposeRawMcpNames:
    """The AgentToolRegistry must never surface raw MCP tool names."""

    def test_tool_names_for_prompt_contain_no_mcp_names(self) -> None:
        """get_tool_names_for_prompt() must return only logical names."""
        registry = AgentToolRegistry.build_default(tenant_id="t_test")
        names = registry.get_tool_names_for_prompt()

        mcp_names_leaked = [n for n in names if n.startswith("chatwoot_")]
        assert (
            mcp_names_leaked == []
        ), f"Raw MCP tool names leaked into prompt tool list (INV-03): {mcp_names_leaked}"

    def test_active_tools_tool_names_differ_from_mcp_names(self) -> None:
        """Each AgentToolDefinition.tool_name must not match any MCP tool name."""
        registry = AgentToolRegistry.build_default(tenant_id="t_test")
        active_tools = registry.get_active_tools()

        mcp_names = {cap.mcp_tool_name for cap in DEFAULT_CHATWOOT_CAPABILITIES}
        violations = [t.tool_name for t in active_tools if t.tool_name in mcp_names]
        assert (
            violations == []
        ), f"AgentToolDefinition.tool_name equals a raw MCP name (INV-03): {violations}"

    def test_lc_tool_definitions_use_logical_names_only(self) -> None:
        """_build_lc_tool_definitions() must emit logical function names."""
        registry = AgentToolRegistry.build_default(tenant_id="t_test")
        active_tools = registry.get_active_tools()
        defs = _build_lc_tool_definitions(active_tools)

        for d in defs:
            fn_name = d["function"]["name"]
            assert not fn_name.startswith(
                "chatwoot_"
            ), f"Raw MCP name {fn_name!r} leaked into LLM tool definition (INV-03)"


@pytest.mark.unit
@pytest.mark.security
class TestMcpRegistryIntentsAreLogical:
    """ChatwootMcpRegistry must expose logical intents, never MCP tool names."""

    def test_active_intents_are_not_mcp_tool_names(self) -> None:
        """list_active_intents() must return intent strings, not chatwoot_* names."""
        registry = ChatwootMcpRegistry(tenant_id="t1", account_id_ref="acc1")
        intents = registry.list_active_intents()

        mcp_names = {cap.mcp_tool_name for cap in DEFAULT_CHATWOOT_CAPABILITIES}
        leaked = set(intents) & mcp_names
        assert leaked == set(), f"Intents overlap with raw MCP tool names (INV-03): {leaked}"

    def test_resolve_intent_maps_logical_to_internal_mcp(self) -> None:
        """resolve_intent() resolves intent→MCP internally; the MCP name is in
        CapabilityResolution.resolved_tool_name (internal use only, never sent to LLM)."""
        registry = ChatwootMcpRegistry(tenant_id="t1", account_id_ref="acc1")
        resolution = registry.resolve_intent("send_message", "t1", "acc1")

        # Sanity: resolution exists and is allowed
        assert resolution.allowed is True
        # The resolved_tool_name is the real Chatwoot MCP tool name — internal only
        assert resolution.resolved_tool_name == "conversation_message_send"
        # But the intent the agent used is the logical name
        assert resolution.intent == "send_message"
        assert not resolution.intent.startswith("chatwoot_")


@pytest.mark.unit
@pytest.mark.security
class TestSystemPromptContainsNoMcpNames:
    """The system prompt built for the agent must be free of raw MCP tool names."""

    @pytest.mark.asyncio
    async def test_system_prompt_excludes_mcp_names(self) -> None:
        """context_builder_node must not embed any chatwoot_* name in the prompt."""
        from iara.graph.nodes.context_builder import context_builder_node

        registry = AgentToolRegistry.build_default(tenant_id="t_test")
        state: dict = {
            "messages": [{"role": "user", "content": "Olá"}],
            "tenant_id": "t_test",
            "conversation_id": "conv_1",
            "metadata": {},
            "step_count": 0,
        }

        result = await context_builder_node(state, registry=registry)
        system_prompt = result["metadata"]["system_prompt"]

        mcp_names = [cap.mcp_tool_name for cap in DEFAULT_CHATWOOT_CAPABILITIES]
        found = [n for n in mcp_names if n in system_prompt]
        assert found == [], f"Raw MCP tool names embedded in system prompt (INV-03): {found}"

    @pytest.mark.asyncio
    async def test_lc_tool_definitions_in_state_use_logical_names(self) -> None:
        """lc_tool_definitions stored in metadata must all use logical names."""
        from iara.graph.nodes.context_builder import context_builder_node

        registry = AgentToolRegistry.build_default(tenant_id="t_test")
        state: dict = {
            "messages": [],
            "tenant_id": "t_test",
            "conversation_id": "conv_2",
            "metadata": {},
            "step_count": 0,
        }

        result = await context_builder_node(state, registry=registry)
        defs = result["metadata"]["lc_tool_definitions"]

        for d in defs:
            fn_name = d["function"]["name"]
            assert not fn_name.startswith(
                "chatwoot_"
            ), f"Raw MCP name {fn_name!r} in state lc_tool_definitions (INV-03)"
