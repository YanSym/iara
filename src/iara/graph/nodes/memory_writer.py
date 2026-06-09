"""Memory writer node — extracts and persists key facts after each conversation.

Runs after command_dispatch. When ``iara_memory_enabled=True`` and the
conversation included meaningful content, makes a single focused LLM call
to extract 1-3 facts and stores them in the PostgresMemoryStore.

The node is a no-op when:
- memory is disabled (``iara_memory_enabled=False``)
- no memory_store was injected
- the conversation was ineligible or produced no response

Per the architecture:
- Only sanitized, non-PII content is stored
- Each fact is a short declarative sentence
- TTL defaults to 90 days (configurable)
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from iara.observability.logging import get_logger

if TYPE_CHECKING:
    from iara.memory.postgres_store import PostgresMemoryStore

logger = get_logger(__name__)

# Maximum facts extracted per conversation turn
_MAX_FACTS = 3

# LLM instruction for memory extraction (kept short to minimise tokens)
_EXTRACT_PROMPT = (
    "Você é um extrator de memória. Leia a conversa abaixo e retorne até "
    f"{_MAX_FACTS} fatos curtos e objetivos que seriam úteis em conversas futuras "
    "sobre este contato. Cada fato deve:\n"
    "- Ter no máximo 80 caracteres\n"
    "- Ser uma sentença declarativa simples\n"
    "- Não conter PII (nome completo, CPF, telefone, e-mail)\n"
    "- Ser relevante para o relacionamento comercial\n\n"
    "Formato de resposta: uma linha por fato, começando com '- '.\n"
    "Se não houver fatos relevantes, responda apenas: NENHUM\n\n"
    "CONVERSA:\n{conversation_text}"
)


async def memory_writer_node(
    state: dict[str, Any],
    memory_store: PostgresMemoryStore | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """Extract and persist 1-3 key facts from the completed conversation.

    Args:
        state: Current graph state.
        memory_store: PostgresMemoryStore (no-op when None).
        llm: LLM client for fact extraction (no-op when None).

    Returns:
        dict[str, Any]: Minimal state update (step_count only).
    """
    step = state.get("step_count", 0) + 1

    if memory_store is None or llm is None:
        return {"step_count": step}

    if not getattr(memory_store, "_enabled", False):
        return {"step_count": step}

    # Only write memories when the agent produced a response
    agent_response = state.get("agent_response") or ""
    if not agent_response.strip():
        return {"step_count": step}

    messages = state.get("messages", [])
    conversation_text = _format_conversation(messages, max_turns=10)
    if not conversation_text.strip():
        return {"step_count": step}

    tenant_id = state.get("tenant_id", "")
    conversation_id = state.get("conversation_id", "")
    run_id = state.get("run_id", "")

    log = logger.bind(
        run_id=run_id,
        tenant_ref=tenant_id[:8],
        conversation_id=conversation_id,
    )

    try:
        facts = await _extract_facts(llm, conversation_text, log)
        if not facts:
            return {"step_count": step}

        for i, fact in enumerate(facts):
            key = f"{conversation_id}:fact_{i}"
            await memory_store.store(key=key, content=fact)

        log.info("memory_facts_stored", count=len(facts))

    except Exception as exc:
        # Non-fatal — never block the pipeline over memory writes
        log.warning(
            "memory_writer_error",
            error_code=type(exc).__name__,
            error_summary=str(exc)[:200],
        )

    return {"step_count": step}


def _format_conversation(
    messages: list[dict[str, Any]],
    max_turns: int = 10,
) -> str:
    """Format the last N turns into readable text for extraction.

    Args:
        messages: List of message dicts with role/content.
        max_turns: Maximum number of turns to include.

    Returns:
        str: Formatted conversation text.
    """
    relevant = [
        m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ][-max_turns * 2 :]

    lines = []
    for msg in relevant:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        if role == "user":
            lines.append(f"Contato: {str(content)[:500]}")
        elif role == "assistant":
            lines.append(f"IAra: {str(content)[:500]}")

    return "\n".join(lines)


async def _extract_facts(
    llm: Any,
    conversation_text: str,
    log: Any,
) -> list[str]:
    """Call the LLM to extract facts from the conversation.

    Args:
        llm: LangChain LLM client.
        conversation_text: Formatted conversation text.
        log: Bound logger.

    Returns:
        list[str]: List of extracted facts (empty if none found).
    """
    from langchain_core.messages import HumanMessage

    prompt = _EXTRACT_PROMPT.format(conversation_text=conversation_text[:3000])
    response = await llm.ainvoke([HumanMessage(content=prompt)])

    text = ""
    if hasattr(response, "content"):
        raw = response.content
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, list):
            text = " ".join(block.get("text", "") for block in raw if isinstance(block, dict))

    if not text or "NENHUM" in text.upper():
        return []

    facts = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line and len(line) > 5:
            facts.append(line[:200])
        if len(facts) >= _MAX_FACTS:
            break

    log.debug("memory_facts_extracted", count=len(facts))
    return facts


def build_memory_writer_node(memory_store: Any, llm: Any) -> Any:
    """Build a partial memory_writer_node with injected dependencies.

    Args:
        memory_store: PostgresMemoryStore (can be None for no-op).
        llm: LLM client (can be None for no-op).

    Returns:
        Callable: Node function ready for LangGraph.
    """
    return partial(memory_writer_node, memory_store=memory_store, llm=llm)
