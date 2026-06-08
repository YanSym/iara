"""Chat router — synchronous LLM invocation for local dev/testing.

Exposes POST /chat/{tenant_key} which invokes the LangGraph graph
directly and returns the agent response synchronously. Intended for
the Streamlit dev UI; production traffic uses the webhook → RabbitMQ
→ worker pipeline.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from iara.contracts.errors import FailClosedError
from iara.observability.logging import get_logger
from iara.tenancy.resolver import InMemoryTenantRepository, TenantResolver

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

_test_repository = InMemoryTenantRepository()
_resolver = TenantResolver(repository=_test_repository, cache_ttl_seconds=60)

if os.getenv("IARA_ENV", "development") in ("development", "sandbox"):
    _test_repository.register(
        os.getenv("IARA_DEV_TENANT_KEY", "test_tenant_001"),
        {
            "tenant_id": os.getenv("IARA_DEV_TENANT_ID", "12345678-1234-5678-1234-567812345678"),
            "name": "Dev Tenant",
            "status": "sandbox",
            "provider": "chatwoot",
            "provider_account_id": os.getenv("IARA_DEV_ACCOUNT_ID", "11111"),
        },
    )


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str | None
    conversation_id: str
    run_id: str


def _get_graph(app: Any) -> Any:
    """Return the cached chat graph, building it on first call."""
    if not hasattr(app.state, "chat_graph"):
        from iara.config.settings import get_settings
        from iara.graph.builder import build_production_graph

        app.state.chat_graph = build_production_graph(get_settings())
        logger.info("chat_graph_built")
    return app.state.chat_graph


@router.post(
    "/{tenant_key}",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Synchronous chat — invokes the LangGraph agent and returns the reply",
)
async def chat(
    tenant_key: str,
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Invoke the conversational graph synchronously and return the agent reply.

    Args:
        tenant_key: Tenant key from the URL path.
        body: Chat request with conversation_id and full message history.
        request: FastAPI request (used to access app.state for the graph).

    Returns:
        ChatResponse: Agent reply text and run metadata.
    """
    try:
        tenant_ctx = await _resolver.resolve(tenant_key)
    except FailClosedError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        ) from exc

    run_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    graph = _get_graph(request.app)

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    initial_state = {
        "run_id": run_id,
        "tenant_id": str(tenant_ctx.tenant_id),
        "conversation_id": body.conversation_id,
        "correlation_id": correlation_id,
        "eligibility_status": "pending",
        "media_processed": False,
        "context_built": False,
        "messages": messages,
        "metadata": {"idempotency_key": run_id},
    }

    config = {
        "configurable": {
            "thread_id": f"chat:{tenant_ctx.tenant_id}:{body.conversation_id}:{run_id}"
        }
    }

    try:
        result = await graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        logger.error(
            "chat_graph_error", error_code=type(exc).__name__, error_summary=str(exc)[:200]
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if result.get("error"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"]
        )

    reply = result.get("agent_response") or ""
    return ChatResponse(reply=reply, conversation_id=body.conversation_id, run_id=run_id)
