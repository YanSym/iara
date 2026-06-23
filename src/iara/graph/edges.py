"""Conditional edge functions for the conversational graph.

Each function determines which node to route to next based on the
current state. All edge conditions are explicit and documented.
"""

from __future__ import annotations

from typing import Any


def should_continue_after_eligibility(state: dict[str, Any]) -> str:
    """Route after eligibility check.

    Routes admin commands to the command_assistant node; all other accepted
    events proceed to media_understanding.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name.
    """
    if state.get("eligibility_status") != "accepted":
        return "end"
    if state.get("is_admin_command"):
        return "command_assistant"
    return "media_understanding"


def should_continue_after_media(state: dict[str, Any]) -> str:
    """Route after media understanding.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name.
    """
    if state.get("error"):
        return "end"
    return "context_builder"


def should_continue_after_context(state: dict[str, Any]) -> str:
    """Route after context building.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name.
    """
    if state.get("error"):
        return "end"
    return "agent"


def should_continue_after_agent(state: dict[str, Any]) -> str:
    """Route after agent response.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name — tool_executor if there are tool calls, else guardrails.
    """
    if state.get("error"):
        return "end"
    if state.get("hitl_requested"):
        return "hitl_interrupt"
    if state.get("tool_calls_pending"):
        return "tool_executor"
    return "guardrails"


def should_continue_after_tools(state: dict[str, Any]) -> str:
    """Route after tool execution.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name — always re-invokes agent so it can react to tool
        results and decide whether to call more tools or produce a final answer.
        The agent node is responsible for terminating the loop (by not emitting
        new tool_calls_pending), at which point should_continue_after_agent
        routes to guardrails.
    """
    if state.get("error"):
        return "end"
    return "agent"


def should_continue_after_guardrails(state: dict[str, Any]) -> str:
    """Route after guardrails.

    Args:
        state: Current graph state.

    Returns:
        str: Next node name — hitl if an escalation was triggered, else command_dispatch.
    """
    if state.get("error"):
        return "end"
    if state.get("hitl_requested"):
        return "hitl"
    return "command_dispatch"


def should_continue_after_dispatch(state: dict[str, Any]) -> str:
    """Route after command dispatch.

    Routes to memory_writer on success. The memory_writer_node is a no-op
    when the memory store is disabled, so routing unconditionally on success
    is safe and avoids threading a 'memory_enabled' flag through state.
    On error, terminates immediately to prevent cascading into memory_writer.

    Args:
        state: Current graph state.

    Returns:
        str: 'memory_writer' on success, 'end' on error.
    """
    if state.get("error"):
        return "end"
    return "memory_writer"
