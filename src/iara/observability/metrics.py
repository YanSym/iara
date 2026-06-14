"""Prometheus metrics registry for the IAra runtime.

All metrics are defined here as module-level singletons so they are
registered exactly once regardless of import order. Instruments:

  - Webhook requests (total, duration, status)
  - Tool invocations (total, duration, by tool_name and status)
  - Outbox command executions (total, by status)
  - Provider readback confirmations (total, by outcome)
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from prometheus_client import REGISTRY as _REGISTRY  # noqa: F401 — re-exported for tests

__all__ = [
    "CONTENT_TYPE_LATEST",
    "generate_latest",
    "webhook_requests_total",
    "webhook_request_duration_seconds",
    "tool_invocations_total",
    "tool_invocation_duration_seconds",
    "outbox_commands_total",
    "readback_confirmations_total",
]

# ── Webhook ───────────────────────────────────────────────────────────────────

webhook_requests_total = Counter(
    "iara_webhook_requests_total",
    "Total Chatwoot webhook requests received",
    ["status"],  # accepted | rejected | error
)

webhook_request_duration_seconds = Histogram(
    "iara_webhook_request_duration_seconds",
    "Chatwoot webhook request processing time in seconds",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

# ── Tools ─────────────────────────────────────────────────────────────────────

tool_invocations_total = Counter(
    "iara_tool_invocations_total",
    "Total Agent Tool invocations",
    ["tool_name", "status"],  # status: success | policy_blocked | failed
)

tool_invocation_duration_seconds = Histogram(
    "iara_tool_invocation_duration_seconds",
    "Agent Tool invocation latency in seconds",
    ["tool_name"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# ── Outbox ────────────────────────────────────────────────────────────────────

outbox_commands_total = Counter(
    "iara_outbox_commands_total",
    "Total provider commands processed by the outbox drainer",
    ["status"],  # sent | failed | dead_lettered
)

# ── Readback ──────────────────────────────────────────────────────────────────

readback_confirmations_total = Counter(
    "iara_readback_confirmations_total",
    "Total readback confirmation attempts",
    ["outcome"],  # confirmed | failed | skipped
)
