"""LangGraph state definition for the conversational agent graph.

The GraphState is the typed state passed between nodes. Each field annotation
controls how LangGraph merges concurrent state updates.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class GraphState(TypedDict, total=False):
    """LangGraph state for the IAra conversational agent.

    With ``total=False``, all fields are optional so nodes only need to return
    the keys they modify. LangGraph merges each node's output dict into the
    accumulated state — unset keys persist unchanged.

    The ``messages`` field uses ``operator.add`` so any messages a node appends
    are concatenated onto the existing list instead of replacing it.
    """

    # Run identifiers
    run_id: str
    tenant_id: str
    conversation_id: str
    correlation_id: str

    # Pipeline state flags
    eligibility_status: str
    media_processed: bool
    context_built: bool

    # Agent outputs
    agent_response: str | None
    tool_calls_pending: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    response_sent: bool
    error: str | None
    hitl_requested: bool
    step_count: int

    # Message history — accumulates across nodes with list concatenation
    messages: Annotated[list[dict[str, Any]], operator.add]

    # Non-sensitive run metadata
    metadata: dict[str, Any]
