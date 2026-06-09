"""LangGraph conversational graph builder.

Assembles the stateful conversational agent graph from named, testable nodes
connected by conditional edges.

Graph structure:
  START → eligibility → (admin?) → command_assistant → END
                      → media_understanding → context_builder → agent
                        ↓ (tool calls)
                        → tool_executor → agent (loop)
                        ↓ (done)
                        → guardrails → command_dispatch → (memory_enabled?) → memory_writer → END
                                                                             → END
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
from iara.graph.nodes.command_assistant import command_assistant_node
from iara.graph.nodes.command_dispatch import command_dispatch_node
from iara.graph.nodes.context_builder import context_builder_node
from iara.graph.nodes.eligibility import build_eligibility_node
from iara.graph.nodes.guardrails import guardrails_node
from iara.graph.nodes.memory_writer import build_memory_writer_node
from iara.graph.nodes.tool_executor import tool_executor_node
from iara.graph.state import GraphState
from iara.observability.logging import get_logger

if TYPE_CHECKING:
    from iara.config.settings import Settings

logger = get_logger(__name__)


def build_conversational_graph(
    llm: Any = None,
    registry: Any = None,
    skill_resolver: Any = None,
    gateway: Any = None,
    outbox_service: Any = None,
    media_subgraph: Any = None,
    memory_store: Any = None,
    auth_guard: Any = None,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the IAra conversational agent graph.

    Args:
        llm: The LLM client (optional; uses stub if None).
        registry: AgentToolRegistry (optional; no tools if None).
        skill_resolver: ToolSkillResolver (optional).
        gateway: AgentToolMcpGateway (optional; uses stub if None).
        outbox_service: Outbox service (optional; uses stub if None).
        media_subgraph: MediaUnderstandingSubgraph (optional; skips media if None).
        memory_store: PostgresMemoryStore (optional; skips memory if None).
        auth_guard: CommandAuthorizationGuard (optional; disables admin routing if None).
        checkpointer: LangGraph checkpointer (optional; uses in-memory if None).

    Returns:
        CompiledGraph: The compiled LangGraph graph.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Inject dependencies into nodes via partial
    eligibility_fn = build_eligibility_node(auth_guard=auth_guard)
    agent_fn = partial(agent_node, llm=llm)
    context_fn = partial(
        context_builder_node,
        registry=registry,
        skill_resolver=skill_resolver,
        memory_store=memory_store,
    )
    tool_fn = partial(tool_executor_node, gateway=gateway)
    dispatch_fn = partial(command_dispatch_node, outbox_service=outbox_service)
    media_fn = partial(_media_understanding_node, media_subgraph=media_subgraph)
    memory_fn = build_memory_writer_node(memory_store=memory_store, llm=llm)

    workflow = StateGraph(GraphState)

    workflow.add_node("eligibility", eligibility_fn)  # type: ignore[type-var]
    workflow.add_node("command_assistant", command_assistant_node)  # type: ignore[type-var]
    workflow.add_node("media_understanding", media_fn)  # type: ignore[type-var]
    workflow.add_node("context_builder", context_fn)  # type: ignore[type-var]
    workflow.add_node("agent", agent_fn)
    workflow.add_node("tool_executor", tool_fn)
    workflow.add_node("guardrails", guardrails_node)  # type: ignore[type-var]
    workflow.add_node("command_dispatch", dispatch_fn)
    workflow.add_node("memory_writer", memory_fn)  # type: ignore[type-var]

    workflow.add_edge(START, "eligibility")

    workflow.add_conditional_edges(
        "eligibility",
        should_continue_after_eligibility,
        {
            "command_assistant": "command_assistant",
            "media_understanding": "media_understanding",
            "end": END,
        },
    )
    workflow.add_edge("command_assistant", END)
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
            "hitl_interrupt": END,
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
        {"memory_writer": "memory_writer"},
    )
    workflow.add_edge("memory_writer", END)

    graph = workflow.compile(checkpointer=checkpointer)
    logger.info("conversational_graph_compiled")
    return graph


def build_production_graph(settings: Settings, checkpointer: Any = None) -> Any:
    """Build the graph with all real dependencies injected from settings.

    Creates and wires:
    - LLM client (Anthropic or OpenAI)
    - AgentToolRegistry + ToolSkillResolver
    - ToolPolicyGuard + ToolExecutor + AgentToolMcpGateway
    - OutboxService (backed by async Postgres session factory)
    - MediaUnderstandingSubgraph (Whisper + GPT-4o vision + pypdf)
    - PostgresMemoryStore for GovernedMemoryStore (when iara_memory_enabled)
    - CommandAuthorizationGuard for admin command routing
    - SchedulingAdapter (Google Calendar, Clinicorp, or Null fallback)

    Args:
        settings: Validated application settings.
        checkpointer: Optional pre-built checkpointer. When None, uses MemorySaver.
            In production, pass an AsyncPostgresSaver from persistence.checkpointer.

    Returns:
        CompiledGraph: Ready-to-invoke graph with all dependencies wired.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from iara.llm.factory import build_llm
    from iara.media.subgraph import MediaUnderstandingSubgraph
    from iara.memory.postgres_store import PostgresMemoryStore
    from iara.persistence.services.outbox_service import OutboxService
    from iara.provider.scheduling.factory import build_scheduling_adapter
    from iara.security.command_auth import CommandAuthorizationGuard, CommandRequesterBinding
    from iara.tools.executor import ToolExecutor
    from iara.tools.gateway import AgentToolMcpGateway
    from iara.tools.policy_guard import OperationMode, ToolPolicyGuard
    from iara.tools.registry import AgentToolRegistry
    from iara.tools.skill_resolver import ToolSkillResolver

    # ── LLM ───────────────────────────────────────────────────────────────────
    llm = build_llm(settings)

    # ── Tool stack ────────────────────────────────────────────────────────────
    registry = AgentToolRegistry.build_default(tenant_id="__production__")
    skill_resolver = ToolSkillResolver()

    kanban_mode = OperationMode(settings.iara_kanban_default_mode.value)
    campaign_mode = OperationMode(settings.iara_campaign_default_mode.value)
    policy_guard = ToolPolicyGuard(
        tenant_id="__production__",
        kanban_mode=kanban_mode,
        campaign_mode=campaign_mode,
    )

    executor = ToolExecutor(tenant_id="__production__")
    gateway = AgentToolMcpGateway(
        registry=registry,
        policy_guard=policy_guard,
        executor=executor,
    )

    # ── Database / Outbox ─────────────────────────────────────────────────────
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    outbox_service = OutboxService(session_factory=session_factory)
    executor._outbox = outbox_service  # type: ignore[attr-defined]

    # ── Media ─────────────────────────────────────────────────────────────────
    media_subgraph = MediaUnderstandingSubgraph(
        openai_api_key=settings.openai_api_key,
        audio_transcription_enabled=settings.iara_audio_transcription_enabled,
        vision_enabled=settings.iara_vision_enabled,
        document_extraction_enabled=settings.iara_document_extraction_enabled,
        max_media_size_mb=settings.iara_media_max_size_mb,
    )

    # ── Semantic memory ───────────────────────────────────────────────────────
    memory_store: PostgresMemoryStore | None = None
    if settings.iara_memory_enabled:
        memory_store = PostgresMemoryStore(
            session_factory=session_factory,
            tenant_id="__production__",
            namespace=settings.iara_memory_namespace,
            enabled=True,
            ttl_days=settings.iara_memory_ttl_days,
        )

    # ── Admin command authorization ───────────────────────────────────────────
    auth_guard = CommandAuthorizationGuard()
    # Register a permissive default binding; per-tenant overrides in Phase 8.
    auth_guard.register(CommandRequesterBinding(tenant_id="__production__"))

    # ── Scheduling adapter ────────────────────────────────────────────────────
    # Inject into the scheduling catalog module so handle_availability can use it.
    scheduling_adapter = build_scheduling_adapter(settings)
    _inject_scheduling_adapter(scheduling_adapter)

    logger.info(
        "production_graph_wired",
        llm_provider=settings.llm_provider,
        active_tools=len(registry.get_active_tools()),
        audio_enabled=settings.iara_audio_transcription_enabled,
        vision_enabled=settings.iara_vision_enabled,
        doc_enabled=settings.iara_document_extraction_enabled,
        memory_enabled=settings.iara_memory_enabled,
        scheduling_provider=scheduling_adapter.provider_name,
        checkpointer_type="postgres" if checkpointer is not None else "memory",
    )

    return build_conversational_graph(
        llm=llm,
        registry=registry,
        skill_resolver=skill_resolver,
        gateway=gateway,
        outbox_service=outbox_service,
        media_subgraph=media_subgraph,
        memory_store=memory_store,
        auth_guard=auth_guard,
        checkpointer=checkpointer,
    )


def _inject_scheduling_adapter(adapter: Any) -> None:
    """Inject the scheduling adapter into the catalog module singleton."""
    try:
        from iara.tools.catalog import scheduling as sched_module

        sched_module._SCHEDULING_ADAPTER = adapter
    except Exception:
        pass  # Non-fatal if catalog module isn't structured yet


async def _media_understanding_node(
    state: dict[str, Any],
    media_subgraph: Any = None,
) -> dict[str, Any]:
    """Media understanding node — processes attachments before the agent.

    Reads attachment metadata from state["metadata"]["attachments"] (list of
    dicts with url, content_type, type, ref keys). Stores MediaContext results
    in state["metadata"]["media_contexts"] for the context_builder to use.

    Args:
        state: Current graph state.
        media_subgraph: MediaUnderstandingSubgraph (optional; skips if None).

    Returns:
        dict[str, Any]: Updated state with media_processed flag.
    """
    metadata = state.get("metadata", {})
    attachment_dicts: list[dict[str, Any]] = metadata.get("attachments", [])

    if not attachment_dicts or media_subgraph is None:
        return {
            "media_processed": True,
            "step_count": state.get("step_count", 0) + 1,
        }

    logger.info(
        "node_media_understanding_start",
        run_id=state.get("run_id"),
        attachment_count=len(attachment_dicts),
    )

    try:
        media_contexts = await media_subgraph.process_from_dicts(attachment_dicts)
        ctx_dicts = [ctx.model_dump() for ctx in media_contexts]

        logger.info(
            "node_media_understanding_complete",
            run_id=state.get("run_id"),
            processed=len(ctx_dicts),
            statuses=[c.get("status") for c in ctx_dicts],
        )

        return {
            "media_processed": True,
            "step_count": state.get("step_count", 0) + 1,
            "metadata": {
                **metadata,
                "media_contexts": ctx_dicts,
            },
        }
    except Exception as exc:
        logger.error(
            "node_media_understanding_failed",
            run_id=state.get("run_id"),
            error_code=type(exc).__name__,
        )
        # Non-fatal: proceed without media context rather than failing the whole run
        return {
            "media_processed": True,
            "step_count": state.get("step_count", 0) + 1,
            "metadata": {
                **metadata,
                "media_contexts": [],
            },
        }
