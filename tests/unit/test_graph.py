"""Unit tests for the LangGraph conversational graph.

Tests use mocked LLM and stubbed dependencies — no real LLM calls, no real
network, no real database. All tests complete in < 1 second each.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from iara.graph.builder import build_conversational_graph


@pytest.mark.unit
class TestGraphBuilder:
    """Tests for build_conversational_graph."""

    def test_graph_compiles_with_no_dependencies(self) -> None:
        """Graph must compile even when all optional deps are None."""
        graph = build_conversational_graph()
        assert graph is not None

    def test_graph_compiles_with_stub_llm(self) -> None:
        """Graph must compile when a stub LLM object is provided."""
        stub_llm = MagicMock()
        graph = build_conversational_graph(llm=stub_llm)
        assert graph is not None

    @pytest.mark.asyncio
    async def test_graph_invokes_and_returns_state(self) -> None:
        """Graph must invoke without error and return a dict."""
        graph = build_conversational_graph()

        initial_state = {
            "run_id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "conv_001",
            "correlation_id": str(uuid.uuid4()),
            "eligibility_status": "pending",
            "media_processed": False,
            "context_built": False,
            "messages": [],
            "metadata": {},
        }
        config = {"configurable": {"thread_id": "test_thread_001"}}

        result = await graph.ainvoke(initial_state, config=config)

        # LangGraph StateGraph(dict) returns a merged state dict — must be non-empty
        assert isinstance(result, dict)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_graph_sets_step_count(self) -> None:
        """Graph must increment step_count after running."""
        graph = build_conversational_graph()

        initial_state = {
            "run_id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "conv_002",
            "correlation_id": str(uuid.uuid4()),
            "eligibility_status": "pending",
            "media_processed": False,
            "context_built": False,
            "messages": [],
            "metadata": {},
        }
        config = {"configurable": {"thread_id": "test_thread_002"}}

        result = await graph.ainvoke(initial_state, config=config)

        assert result.get("step_count", 0) >= 1

    @pytest.mark.asyncio
    async def test_graph_completes_without_errors(self) -> None:
        """Graph must complete all nodes without raising an exception."""
        graph = build_conversational_graph()

        initial_state = {
            "run_id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "conversation_id": "conv_003",
            "correlation_id": str(uuid.uuid4()),
            "eligibility_status": "pending",
            "media_processed": False,
            "context_built": False,
            "messages": [],
            "metadata": {},
        }
        config = {"configurable": {"thread_id": "test_thread_003"}}

        # Must not raise — stub nodes should return partial state updates cleanly
        result = await graph.ainvoke(initial_state, config=config)
        assert isinstance(result, dict)
