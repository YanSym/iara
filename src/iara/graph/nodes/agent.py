"""Agent node — invokes the LLM to generate a response or tool calls.

The agent node is the core orchestrator. It:
1. Prepends the system prompt built by context_builder.
2. Binds active tool definitions to the LLM (INV-03: only logical names).
3. Invokes the LLM with the full message history.
4. Appends the AI message (with or without tool calls) to state messages so
   the tool-calling loop has the correct multi-turn history.

Per INV-03: the LLM never sees raw MCP tool names or the Chatwoot catalog.
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Maximum number of tool-calling iterations before forcing a text response
MAX_TOOL_ITERATIONS = 5


def _to_lc_messages(messages: list[dict[str, Any]]) -> list[Any]:
    """Convert dict messages to LangChain message objects.

    Handles user, assistant (with optional tool_calls), tool result, and system
    messages. LangChain normalises these for both OpenAI and Anthropic providers.

    Args:
        messages: List of message dicts.

    Returns:
        list: LangChain BaseMessage objects.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                result.append(AIMessage(content=content, tool_calls=tool_calls))
            else:
                result.append(AIMessage(content=content))
        elif role == "tool":
            result.append(
                ToolMessage(
                    content=content,
                    tool_call_id=msg.get("tool_call_id", ""),
                )
            )
    return result


def _extract_text(content: Any) -> str | None:
    """Normalise LLM response content to a plain string.

    Handles both OpenAI (str) and Anthropic (list of content blocks) formats.

    Args:
        content: The raw content from the LLM response.

    Returns:
        str | None: Plain text content, or None if empty.
    """
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts).strip() or None
    return None


async def agent_node(state: dict[str, Any], llm: Any = None) -> dict[str, Any]:
    """Invoke the LLM and update graph state with the response.

    Args:
        state: Current graph state.
        llm: The LLM client (injected; optional in tests).

    Returns:
        dict[str, Any]: State updates with agent_response, tool_calls_pending,
                        and any new messages to append.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")
    metadata = state.get("metadata", {})

    logger.info(
        "node_agent_start",
        run_id=run_id,
        correlation_id=correlation_id,
    )

    messages = state.get("messages", [])

    if llm is None:
        # Test stub — return a canned response without touching messages
        return {
            "agent_response": "Olá! Como posso ajudar você hoje?",
            "tool_calls_pending": [],
            "step_count": state.get("step_count", 0) + 1,
        }

    # Guard against infinite tool-calling loops
    step_count = state.get("step_count", 0)
    if step_count >= MAX_TOOL_ITERATIONS * 2:
        logger.warning(
            "node_agent_max_iterations",
            run_id=run_id,
            step_count=step_count,
        )
        return {
            "agent_response": (
                "Desculpe, não consegui completar a operação. Por favor, tente novamente."
            ),
            "tool_calls_pending": [],
            "step_count": step_count + 1,
        }

    # ── Build LLM input ───────────────────────────────────────────────────────
    system_prompt = metadata.get("system_prompt")
    lc_tool_defs = metadata.get("lc_tool_definitions", [])

    llm_input: list[dict[str, Any]] = []
    if system_prompt:
        llm_input.append({"role": "system", "content": system_prompt})
    llm_input.extend(messages)

    # Convert to LangChain message objects for proper multi-turn handling
    lc_messages = _to_lc_messages(llm_input)

    # ── Bind tools if available ───────────────────────────────────────────────
    bound_llm = llm.bind_tools(lc_tool_defs) if lc_tool_defs else llm

    try:
        response = await bound_llm.ainvoke(lc_messages)
    except Exception as exc:
        logger.error(
            "node_agent_failed",
            run_id=run_id,
            correlation_id=correlation_id,
            error_code=type(exc).__name__,
        )
        return {
            "error": f"Agent execution failed: {type(exc).__name__}",
            "step_count": state.get("step_count", 0) + 1,
        }

    # ── Parse response ────────────────────────────────────────────────────────
    agent_response = _extract_text(response.content)
    tool_calls_pending: list[dict[str, Any]] = []
    messages_to_append: list[dict[str, Any]] = []

    raw_tool_calls = getattr(response, "tool_calls", None) or []

    if raw_tool_calls:
        # LLM wants to call tools — build pending list and append AI message
        for tc in raw_tool_calls:
            tool_calls_pending.append(
                {
                    "call_id": tc.get("id", ""),
                    "tool_name": tc.get("name", ""),
                    "arguments": tc.get("args", {}),
                }
            )

        # Append the AI tool-call request to the message history so the next
        # agent invocation has the full multi-turn context.
        messages_to_append.append(
            {
                "role": "assistant",
                "content": agent_response or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                        "type": "tool_call",
                    }
                    for tc in raw_tool_calls
                ],
            }
        )

        logger.info(
            "node_agent_tool_calls",
            run_id=run_id,
            correlation_id=correlation_id,
            tool_count=len(tool_calls_pending),
            tool_names=[tc["tool_name"] for tc in tool_calls_pending],
        )
    else:
        # Final text response — append as assistant message
        if agent_response:
            messages_to_append.append({"role": "assistant", "content": agent_response})

        logger.info(
            "node_agent_complete",
            run_id=run_id,
            correlation_id=correlation_id,
            has_response=bool(agent_response),
        )

    result: dict[str, Any] = {
        "agent_response": agent_response,
        "tool_calls_pending": tool_calls_pending,
        "step_count": state.get("step_count", 0) + 1,
    }
    if messages_to_append:
        result["messages"] = messages_to_append  # appended via operator.add reducer

    return result
