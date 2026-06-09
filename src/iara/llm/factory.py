"""LLM factory — builds the right client with the right parameters.

Provider selection and model-family detection live here so that the rest
of the runtime never imports provider SDKs directly.

Model families and their parameter contracts:

  Anthropic (any Claude model)
    → max_tokens = settings.max_tokens

  OpenAI family 4  — nome contém "4"  (gpt-4o, gpt-4o-mini, o4-mini, gpt-4-turbo…)
    → temperature = 0

  OpenAI family 5  — nome contém "5"  (gpt-5, o5, o5-mini…)
    → reasoning_effort = "low"
    → temperature is NOT set (unsupported by the API)

A detecção é feita pelo número presente no nome do modelo:
  "4" no nome → família 4   "5" no nome → família 5
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from iara.config.settings import LlmProvider
from iara.observability.logging import get_logger

if TYPE_CHECKING:
    from iara.config.settings import Settings

logger = get_logger(__name__)


def _model_family(model: str) -> int:
    """Return the numeric family of an OpenAI model (4 or 5).

    Rule: if the model name contains "5" → family 5; if it contains "4" →
    family 4.  Defaults to 4 so unknown models get the safer temperature=0.

    Examples
    --------
    gpt-4o       → 4   gpt-4o-mini  → 4   o4-mini → 4
    gpt-5        → 5   o5-mini      → 5
    """
    if "5" in model:
        return 5
    if "4" in model:
        return 4
    return 4  # safe default


def _resolve_api_key(direct_key: str | None, key_ref: str) -> str | None:
    """Return the direct key when provided; otherwise resolve from secret store.

    Handles ``secret://namespace/key`` refs by converting the path to an
    uppercase env var (e.g. ``secret://anthropic/api_key`` → ``ANTHROPIC_API_KEY``).
    When the env var is absent, returns None so the SDK falls back to its own
    standard env-var lookup (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``).
    """
    if direct_key:
        return direct_key
    if not key_ref or not key_ref.startswith("secret://"):
        return None
    path = key_ref[len("secret://") :]
    env_key = path.upper().replace("/", "_").replace("-", "_")
    return os.environ.get(env_key)


def build_llm(settings: Settings) -> Any:
    """Build and return the configured LLM client.

    Reads ``llm_provider`` from settings and delegates to the
    appropriate builder.  Raises ``ImportError`` if the required
    provider package is not installed.

    Args:
        settings: Application settings (already loaded and validated).

    Returns:
        A LangChain chat model instance ready for ``ainvoke()``.
    """
    if settings.llm_provider == LlmProvider.OPENAI:
        return _build_openai(settings)
    return _build_anthropic(settings)


# ── Anthropic ─────────────────────────────────────────────────────────────────


def _build_anthropic(settings: Settings) -> Any:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ImportError(
            "langchain-anthropic is required for the Anthropic provider. "
            "Run: uv add langchain-anthropic"
        ) from exc

    api_key = _resolve_api_key(settings.anthropic_api_key, settings.anthropic_api_key_ref)
    model = settings.anthropic_model

    logger.info("llm_init", provider="anthropic", model=model)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": settings.max_tokens,
    }
    if api_key:
        kwargs["api_key"] = api_key

    return ChatAnthropic(**kwargs)


# ── OpenAI ────────────────────────────────────────────────────────────────────


def _build_openai(settings: Settings) -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "langchain-openai is required for the OpenAI provider. " "Run: uv add langchain-openai"
        ) from exc

    api_key = _resolve_api_key(settings.openai_api_key, settings.openai_api_key_ref)
    model = settings.openai_model
    family = _model_family(model)

    kwargs: dict[str, Any] = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key

    if family == 5:
        kwargs["model_kwargs"] = {"reasoning_effort": "low"}
        logger.info(
            "llm_init", provider="openai", model=model, family=5, param="reasoning_effort=low"
        )
    else:
        kwargs["temperature"] = 0
        logger.info("llm_init", provider="openai", model=model, family=4, param="temperature=0")

    return ChatOpenAI(**kwargs)
