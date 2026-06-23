"""Guardrails node — normalizes and validates agent response before dispatch.

Applies safety checks to the agent's output, in order:
1. Strip leaked internal prompts / chain-of-thought markers.
2. Blocklist check — if the response contains any globally banned term,
   replace it with the neutral refusal message (never forward the original).
3. Anti-loop detection — compare to recent responses using difflib similarity;
   a ratio >= LOOP_THRESHOLD counts as a near-duplicate and triggers HITL hold.
4. Low-confidence guard — if the response contains hedging patterns that
   indicate the model is unsure, escalate to HITL.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from iara.observability.logging import get_logger
from iara.security.content_filter import BLOCKED_RESPONSE, content_filter

logger = get_logger(__name__)

# Patterns that indicate potential prompt leakage
INTERNAL_MARKERS = [
    "<system>",
    "</system>",
    "<internal>",
    "chain-of-thought",
    "TOOL_CALL:",
    "INTERNAL:",
]

# Ratio above which two responses are considered near-duplicates.
LOOP_THRESHOLD = 0.85

# How many recent responses to compare against for loop detection.
LOOP_WINDOW = 3

# Phrases that signal the model is operating outside its confidence boundary.
_LOW_CONFIDENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi (don'?t|do not) know\b", re.IGNORECASE),
    re.compile(r"\bi'?m not sure\b", re.IGNORECASE),
    re.compile(r"\bi cannot (be certain|confirm|guarantee)\b", re.IGNORECASE),
    re.compile(r"\bI (may|might) be wrong\b", re.IGNORECASE),
    re.compile(
        r"\byou (should|may want to) (consult|ask|verify|check with)"
        r" (a (doctor|lawyer|professional|specialist|human))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI don'?t have (enough|sufficient|the right) (information|data|context)\b",
        re.IGNORECASE,
    ),
]

# Neutral escalation response shown to the end-user when a hold is triggered.
_HITL_ESCALATION_RESPONSE = (
    "I want to make sure I handle this correctly. "
    "Let me connect you with a team member who can help further."
)


async def guardrails_node(state: dict[str, Any]) -> dict[str, Any]:
    """Apply guardrails to the agent response.

    Args:
        state: Current graph state.

    Returns:
        dict[str, Any]: State updates with sanitized agent_response.
    """
    run_id = state.get("run_id")
    correlation_id = state.get("correlation_id")
    agent_response = state.get("agent_response", "")

    logger.info(
        "node_guardrails_start",
        run_id=run_id,
        correlation_id=correlation_id,
    )

    if not agent_response:
        return {"step_count": state.get("step_count", 0) + 1}

    # 1. Strip internal content leakage
    sanitized = _strip_internal_markers(agent_response)
    if sanitized != agent_response:
        logger.warning(
            "guardrails_stripped_internal_content",
            run_id=run_id,
            correlation_id=correlation_id,
        )

    # 2. Blocklist check — replace entirely if any banned term is found.
    if content_filter.contains_blocked_content(sanitized):
        matched = content_filter.first_match(sanitized)
        logger.warning(
            "guardrails_blocklist_triggered",
            run_id=run_id,
            correlation_id=correlation_id,
            matched_pattern=matched,
        )
        return {
            "agent_response": BLOCKED_RESPONSE,
            "step_count": state.get("step_count", 0) + 1,
        }

    # 3. Anti-loop detection
    response_history: list[str] = state.get("response_history", [])
    if _is_looping(sanitized, response_history):
        logger.warning(
            "guardrails_loop_detected",
            run_id=run_id,
            correlation_id=correlation_id,
            history_length=len(response_history),
        )
        updated_history = (response_history + [sanitized])[-LOOP_WINDOW:]
        return {
            "agent_response": _HITL_ESCALATION_RESPONSE,
            "hitl_requested": True,
            "hitl_reason": "anti_loop",
            "response_history": updated_history,
            "step_count": state.get("step_count", 0) + 1,
        }

    # 4. Low-confidence guard
    if _is_low_confidence(sanitized):
        logger.warning(
            "guardrails_low_confidence",
            run_id=run_id,
            correlation_id=correlation_id,
        )
        updated_history = (response_history + [sanitized])[-LOOP_WINDOW:]
        return {
            "agent_response": _HITL_ESCALATION_RESPONSE,
            "hitl_requested": True,
            "hitl_reason": "low_confidence",
            "response_history": updated_history,
            "step_count": state.get("step_count", 0) + 1,
        }

    updated_history = (response_history + [sanitized])[-LOOP_WINDOW:]
    return {
        "agent_response": sanitized,
        "response_history": updated_history,
        "step_count": state.get("step_count", 0) + 1,
    }


def _strip_internal_markers(text: str) -> str:
    """Remove lines containing internal marker patterns from the response."""
    result = text
    for marker in INTERNAL_MARKERS:
        if marker.lower() in result.lower():
            lines = result.split("\n")
            lines = [line for line in lines if marker.lower() not in line.lower()]
            result = "\n".join(lines)
    return result.strip()


def _is_looping(response: str, history: list[str]) -> bool:
    """Return True if *response* is near-identical to a recent response.

    Uses difflib.SequenceMatcher with LOOP_THRESHOLD to detect paraphrased
    repetitions. Compares against the last LOOP_WINDOW responses only.

    Args:
        response: The current sanitized response.
        history: List of previous response strings (oldest first).

    Returns:
        bool: True when a loop is detected.
    """
    if not history:
        return False

    response_lower = response.lower()
    recent = history[-LOOP_WINDOW:]

    for past in recent:
        ratio = difflib.SequenceMatcher(None, response_lower, past.lower(), autojunk=False).ratio()
        if ratio >= LOOP_THRESHOLD:
            return True
    return False


def _is_low_confidence(response: str) -> bool:
    """Return True if *response* contains low-confidence hedging patterns.

    Args:
        response: The sanitized response text.

    Returns:
        bool: True when the response signals the model is outside its scope.
    """
    return any(pattern.search(response) for pattern in _LOW_CONFIDENCE_PATTERNS)
