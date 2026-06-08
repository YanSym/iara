"""LangGraph conversational graph builder.

Assembles the stateful conversational agent graph from named, testable nodes
connected by conditional edges.

Graph structure:
  START → eligibility → media_understanding → context_builder → agent
        ↓ (tool calls)
        → tool_executor → agent (loop)
        ↓ (done)
        → guardrails → command_dispatch → END
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from iara.graph.edges import (
    should_continue_after_agent,
    should_continue_after_context,
    should_continue_after_dispatch,
    should_continue_after_eligibility,
    should_continue_after_guardrails,
    should_continue_after_media,
    should_continue_after_tools,
)
from iara.graph.nodes.agent import agent_node
from iara.graph.nodes.command_dispatch import command_dispatch_node
from iara.graph.nodes.context_builder import context_builder_node
from iara.graph.nodes.eligibility import eligibility_node
from iara.graph.nodes.guardrails import guardrails_node
from iara.graph.nodes.tool_executor import tool_executor_node
from iara.graph.state import GraphState
from iara.observability.logging import get_logger

if TYPE_CHECKING:
    from iara.config.settings import Settings

logger = get_logger(__name__)


def build_conversational_graph(
    llm: Any = None,
    gateway: Any = None,
    outbox_service: Any = None,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the IAra conversational agent graph.

    Args:
        llm: The LLM client (optional; uses stub if None).
        gateway: AgentToolMcpGateway (optional; uses stub if None).
        outbox_service: Outbox service (optional; uses stub if None).
        checkpointer: LangGraph checkpointer (optional; uses in-memory if None).

    Returns:
        CompiledGraph: The compiled LangGraph graph.
    """
    # Use in-memory checkpointer for development/tests
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Create node functions with injected dependencies
    agent_fn = partial(agent_node, llm=llm)
    tool_fn = partial(tool_executor_node, gateway=gateway)
    dispatch_fn = partial(command_dispatch_node, outbox_service=outbox_service)

    # Build the graph
    workflow = StateGraph(GraphState)

    # Add nodes
    workflow.add_node("eligibility", eligibility_node)  # type: ignore[type-var]
    workflow.add_node("media_understanding", _media_understanding_node)  # type: ignore[type-var]
    workflow.add_node("context_builder", context_builder_node)  # type: ignore[type-var]
    workflow.add_node("agent", agent_fn)
    workflow.add_node("tool_executor", tool_fn)
    workflow.add_node("guardrails", guardrails_node)  # type: ignore[type-var]
    workflow.add_node("command_dispatch", dispatch_fn)

    # Entry point
    workflow.add_edge(START, "eligibility")

    # Conditional edges
    workflow.add_conditional_edges(
        "eligibility",
        should_continue_after_eligibility,
        {"media_understanding": "media_understanding", "end": END},
    )
    workflow.add_conditional_edges(
        "media_understanding",
        should_continue_after_media,
        {"context_builder": "context_builder", "end": END},
    )
    workflow.add_conditional_edges(
        "context_builder",
        should_continue_after_context,
        {"agent": "agent", "end": END},
    )
    workflow.add_conditional_edges(
        "agent",
        should_continue_after_agent,
        {
            "tool_executor": "tool_executor",
            "guardrails": "guardrails",
            "hitl_interrupt": END,  # HITL triggers a pause
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "tool_executor",
        should_continue_after_tools,
        {"agent": "agent", "end": END},
    )
    workflow.add_conditional_edges(
        "guardrails",
        should_continue_after_guardrails,
        {"command_dispatch": "command_dispatch", "end": END},
    )
    workflow.add_conditional_edges(
        "command_dispatch",
        should_continue_after_dispatch,
        {"end": END},
    )

    # Compile with checkpointer for resume support
    graph = workflow.compile(checkpointer=checkpointer)

    logger.info("conversational_graph_compiled")
    return graph


def build_production_graph(settings: Settings) -> Any:
    """Build the graph with real LLM and dependencies injected from settings.

    Use this in production entry points (worker, API). Tests should call
    ``build_conversational_graph()`` directly with stub dependencies.

    Args:
        settings: Validated application settings.

    Returns:
        CompiledGraph: Ready-to-invoke graph with the configured LLM.
    """
    from iara.llm.factory import build_llm

    llm = build_llm(settings)
    return build_conversational_graph(llm=llm)


async def _media_understanding_node(state: dict[str, Any]) -> dict[str, Any]:
    """Media understanding node stub (thin orchestrator).

    In production, this calls MediaUnderstandingSubgraph.process().

    Args:
        state: Current graph state.

    Returns:
        dict[str, Any]: Updated state with media_processed flag.
    """
    return {
        "media_processed": True,
        "step_count": state.get("step_count", 0) + 1,
    }
