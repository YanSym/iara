# IAra — Implementation Plan & Technical Record

> **Status as of 2026-06-08:** Phases 0–7 implemented, all 113 unit + security tests passing.
> Phase 8 (per-tenant MCP catalog + admin surface) is scope-gated — not built until contracted.

---

## 0. Purpose of this document

This document serves two purposes:

1. **Historical record** — Describes what was built, phase by phase, so future contributors understand the reasoning behind every architectural decision.
2. **Continuation guide** — Describes what remains (Phase 8, open decisions) and the gate criteria that must be met before production.

**Golden rule:** This is a security-critical, multi-tenant distributed system. "It looks like it works" is never acceptance. A phase is done only when its gate criteria are proven by automated tests.

---

## 1. Non-negotiable invariants

These invariants apply to every line of code. They cannot be disabled or bypassed.

| # | Name | Rule | Enforcement |
|---|------|------|-------------|
| INV-01 | Fail-Closed | Any ambiguity raises `FailClosedError` before any external call | `tests/security/test_fail_closed.py` |
| INV-02 | No Cross-Tenant | Provider account re-verified before every side effect | `tests/security/test_cross_tenant.py` |
| INV-03 | LLM Never Touches Raw MCP | Agent sees only logical tool names | `AgentToolRegistry` + `ChatwootMcpRegistry` separation |
| INV-04 | Effectively-Once Side Effects | All writes go through the outbox | `OutboxDrainerWorker` + idempotency ledger |
| INV-05 | No PII in Durable Storage | Only hashes, refs, counts in storage and logs | `tests/security/test_redaction.py` |
| INV-06 | High-Risk Writes Are Gated | Campaigns `draft_only`, kanban `suggest_only` by default | `ToolPolicyGuard` |
| INV-07 | Production Is Blocked | Requires `IARA_PRODUCTION_AUTHORIZED=true` | `Settings.is_production` guard |

Full specification: `docs/INVARIANTS.md`. Evidence report: `docs/evidence/invariants_gate_report.md`.

---

## 2. Technology stack

| Concern | Choice | Version |
|---------|--------|---------|
| Python | **3.13** | `requires-python = ">=3.13"` |
| Packaging | **uv** | `uv.lock` committed |
| HTTP | FastAPI + Uvicorn | `>=0.115.0` / `>=0.32.0` |
| Validation | Pydantic v2 | `>=2.10.0` — all domain contracts |
| Settings | pydantic-settings v2 | `>=2.7.0` — env-driven, `*_ref` secret pattern |
| Orchestration | LangGraph | `>=0.2.60` — `StateGraph(dict)`, `MemorySaver`, 7 nodes |
| Queue | RabbitMQ via `aio-pika` | `>=9.5.0` — DLX/retry/backoff wired |
| Database | PostgreSQL via SQLAlchemy 2.0 async + asyncpg | Idempotency, leases, debounce, outbox, audit |
| Migrations | Alembic async | `20260605_0001_initial_schema.py` |
| LLM — Anthropic | `langchain-anthropic` | `>=0.3.0` — default provider |
| LLM — OpenAI | `langchain-openai` | `>=0.3.0` — optional, selectable via `IARA_LLM_PROVIDER` |
| Logging | structlog + `RedactionProcessor` | All log fields pass redaction filter |
| Tests | pytest + pytest-asyncio + pytest-cov | **113 tests, ~3 s** — no external services for unit/security |
| Format | black (100 chars) + ruff | `make format` |
| Lint | ruff + flake8 | `setup.cfg` — max-line-length=120 |
| Types | mypy strict | 0 errors |
| Local infra | docker-compose | Postgres 16 + RabbitMQ 3.13 |
| Test UI | Streamlit | `>=1.40.0` — `make ui` |

### LLM provider details

`IARA_LLM_PROVIDER` selects the active provider at startup. Both providers are fully wired:

**Anthropic:**
- Secret: `ANTHROPIC_API_KEY` (dev) or `ANTHROPIC_API_KEY_REF` (production)
- Model: `IARA_DEFAULT_LLM_MODEL` (default: `claude-sonnet-4-6`)
- Parameters: `max_tokens = IARA_DEFAULT_LLM_MAX_TOKENS`

**OpenAI:**
- Secret: `OPENAI_API_KEY` (dev) or `OPENAI_API_KEY_REF` (production)
- Model: `OPENAI_MODEL` (default: `gpt-4o`)
- Parameters: auto-detected by model name — `4` in name → `temperature=0`; `5` in name → `reasoning_effort=low`

---

## 3. Repository layout (as-built)

```
mcp_platform/
├── src/iara/
│   ├── api/                    # FastAPI app + webhook + admin routers
│   │   ├── app.py              # Factory + lifespan (RabbitMQ connect/disconnect)
│   │   └── routers/
│   │       ├── webhooks.py     # POST /webhooks/chatwoot/{tenant_key}
│   │       │                   # Auto-registers dev tenant on development/sandbox envs
│   │       └── admin.py        # Health + sandbox echo endpoints
│   ├── config/
│   │   └── settings.py         # All settings, LlmProvider enum, parse_origins validator
│   ├── config_publishing/      # Draft→validate→publish pipeline (KB/config)
│   ├── contracts/              # Pydantic v2 domain models
│   │   ├── errors.py           # FailClosedError, CrossTenantError, typed hierarchy
│   │   ├── events.py           # NormalizedChatwootEvent, RawEventRef, EligibilityDecision
│   │   ├── provider.py         # ProviderCommand, CapabilityResolution, RiskClass
│   │   ├── state.py            # GraphState for LangGraph
│   │   ├── tenancy.py          # TenantContext with fail-closed guards
│   │   └── tools.py            # AgentToolDefinition, ToolInvocationRequest/Result
│   ├── eligibility/
│   │   ├── normalizer.py       # Strips PII → NormalizedChatwootEvent (hash-ref only)
│   │   └── decision.py         # 7-rule EligibilityChecker (account/direction/sender/private/idempotency/debounce)
│   ├── graph/                  # LangGraph orchestration
│   │   ├── builder.py          # build_conversational_graph() + build_production_graph(settings)
│   │   ├── edges.py            # Conditional edge functions (all deterministic, no LLM)
│   │   └── nodes/
│   │       ├── eligibility.py      # Re-validates event eligibility inside graph
│   │       ├── context_builder.py  # Assembles governed agent context
│   │       ├── agent.py            # LLM agent node (stub + real)
│   │       ├── tool_executor.py    # Executes agent tool calls via ToolExecutor
│   │       ├── guardrails.py       # Safety/policy checks before dispatch
│   │       └── command_dispatch.py # Enqueues ProviderCommands to outbox
│   ├── llm/
│   │   └── factory.py          # build_llm(settings) — Anthropic or OpenAI with family detection
│   ├── media/
│   │   └── subgraph.py         # MediaUnderstanding subgraph (audio/image/doc)
│   ├── memory/
│   │   └── store.py            # GovernedMemoryStore (draft/publish lifecycle)
│   ├── messaging/
│   │   ├── topology.py         # Exchange/queue/DLX/retry declarations
│   │   ├── publisher.py        # ConversationJob publisher (PERSISTENT delivery)
│   │   └── consumer.py         # Job consumer with DLX/nack
│   ├── observability/
│   │   └── logging.py          # structlog + RedactionProcessor
│   ├── persistence/
│   │   ├── database.py         # Async engine + session factory
│   │   ├── models.py           # 13 runtime + 3 config tables
│   │   └── repositories/
│   │       ├── idempotency.py  # event_receipts — duplicate event prevention
│   │       ├── debounce.py     # conversation_debounce — rapid-fire prevention
│   │       ├── leases.py       # conversation_run_leases — fencing tokens
│   │       └── outbox.py       # provider_command_outbox — effectively-once writes
│   ├── provider/
│   │   ├── adapter.py          # ProviderAdapter Protocol
│   │   ├── capability.py       # CapabilityGateway (fail-closed on unknown intents)
│   │   ├── readback.py         # ReadbackService — confirms mutations applied
│   │   ├── error_mapper.py     # Maps provider errors to typed IaraErrors
│   │   └── chatwoot/
│   │       ├── mcp_adapter.py  # Real Chatwoot MCP adapter
│   │       ├── mcp_registry.py # ChatwootMcpRegistry — intent → MCP tool mapping
│   │       └── fake_mcp.py     # FakeChatwootAdapter for tests
│   ├── security/
│   │   ├── redaction.py        # redact_dict(), RedactionProcessor, SENSITIVE_FIELDS
│   │   └── guards.py           # Fail-closed guard functions
│   ├── tenancy/
│   │   └── resolver.py         # TenantResolver (TTL cache, FailClosedError on miss)
│   ├── tools/
│   │   ├── registry.py         # AgentToolRegistry — 20 tools
│   │   ├── gateway.py          # AgentToolMcpGateway — bridges agent ↔ registry
│   │   ├── policy_guard.py     # ToolPolicyGuard — kanban/campaign/high-risk policy
│   │   ├── executor.py         # ToolExecutor — read/draft/outbox routing
│   │   ├── skill_resolver.py   # SkillResolver for tenant-specific tool config
│   │   └── catalog/            # Per-tool handlers (9 modules, 20 handlers)
│   │       ├── scheduling.py   # availability, schedule, cancel, reschedule
│   │       ├── qualification.py# qualify, disqualify
│   │       ├── kanban.py       # kanban_analyze, kanban_update, kanban_comment
│   │       ├── campaigns.py    # create, validate, approve, dispatch, status, cancel
│   │       ├── followup.py     # followup_reengage_conversation
│   │       ├── kb.py           # kb_suggest_update
│   │       ├── voice.py        # voice_respond_audio
│   │       ├── lead.py         # lead_search
│   │       └── history.py      # history_analyze_conversations
│   └── workers/
│       ├── main.py             # Entrypoint — starts both tasks, thread-safe signal handling
│       ├── job_consumer.py     # RabbitMQ consumer → LangGraph runner (validates payload)
│       └── outbox_drainer.py   # Postgres outbox → provider adapter → readback
│
├── tests/
│   ├── fixtures/
│   │   └── synthetic_events.py # Synthetic Chatwoot payloads (no real data)
│   ├── unit/                   # 98 tests — no external services
│   │   ├── test_contracts.py   # Pydantic contracts, hashing, normalization
│   │   ├── test_eligibility.py # EligibilityChecker 7-rule logic
│   │   ├── test_redaction.py   # redact_dict, RedactionProcessor
│   │   ├── test_tools.py       # AgentToolRegistry, ToolPolicyGuard
│   │   ├── test_catalog_tools.py # All 20 tool handler functions
│   │   ├── test_executor.py    # ToolExecutor read/draft/outbox routing
│   │   ├── test_messaging.py   # Publisher, topology constants (mocked)
│   │   ├── test_graph.py       # LangGraph build + invoke (stub LLM)
│   │   └── test_settings.py    # Settings validation and derived properties
│   ├── security/               # 15 tests — invariant enforcement
│   │   ├── test_cross_tenant.py# INV-02: cross-tenant rejection
│   │   ├── test_fail_closed.py # INV-01: fail-closed on ambiguity
│   │   └── test_redaction.py   # INV-05: no PII in normalized output
│   └── integration/            # Stubs — testcontainers not wired yet
│       └── test_idempotency.py # TODO: wire testcontainers (Postgres + RabbitMQ)
│
├── migrations/
│   └── versions/
│       └── 20260605_0001_initial_schema.py
├── docs/
│   ├── INVARIANTS.md
│   ├── architecture.md
│   ├── configuration.md
│   ├── secrets.md
│   ├── runbook.md
│   ├── rollback.md
│   ├── der.md
│   ├── decisions/OPEN_DECISIONS.md
│   └── evidence/invariants_gate_report.md
├── scripts/init_db.sql         # Postgres bootstrap (extensions + grants)
├── ui.py                       # Streamlit local test UI
├── Dockerfile                  # Multi-stage Python 3.13 image
├── docker-compose.yml          # Postgres + RabbitMQ + api + worker (profiles)
├── Makefile                    # All development commands
├── pyproject.toml              # Python 3.13, dependencies, tooling config
├── setup.cfg                   # flake8 config (max-line-length=120)
├── alembic.ini
├── .env.example                # Full variable reference with safe defaults
└── .env                        # Local secrets (gitignored)
```

---

## 4. Phase completion status

### Phase 0 — Foundations & contracts ✅ COMPLETE

- Project skeleton, `pyproject.toml` (Python 3.13), `uv.lock`, `Makefile`, docker-compose
- All Pydantic v2 contracts: `RawEventRef`, `TenantContext`, `NormalizedChatwootEvent`,
  `EligibilityDecision`, `ConversationState`, `ToolInvocationRequest`, `ToolInvocationResult`,
  `ProviderCommand`, `ProviderMutationResult`, full error hierarchy
- Redaction: `redact_dict()`, `RedactionProcessor`, `SENSITIVE_FIELDS` set
- `docs/INVARIANTS.md`, `docs/decisions/OPEN_DECISIONS.md`
- **Gate evidence:** `test_contracts.py`, `test_redaction.py` (security)

---

### Phase 1 — Provider layer, registry & MCP ✅ COMPLETE

- `ProviderAdapter` protocol
- `ChatwootMcpRegistry` — intent → raw MCP tool name mapping; LLM never sees raw names (INV-03)
- `ChatwootMcpAdapter` + `FakeChatwootAdapter` for tests
- `CapabilityGateway` — fail-closed on unknown/denied intents
- `ProviderErrorMapper` — typed error hierarchy
- `ReadbackService` — confirms mutations applied
- `TenantResolver` with TTL cache
- **Gate evidence:** `test_cross_tenant.py`, `test_fail_closed.py`

---

### Phase 2 — Persistence, queues & operational control ✅ COMPLETE

- Alembic migration `20260605_0001_initial_schema.py` — all runtime tables
- Repositories: `IdempotencyRepository`, `DebounceRepository`, `LeaseRepository`, `OutboxRepository`
- RabbitMQ topology: `iara.jobs` exchange (topic), `iara.jobs.conversation` queue, DLX, retry, backoff
- `MessagePublisher` (PERSISTENT delivery mode) + `MessageConsumer`
- **Gate evidence:** `test_messaging.py`
- **Known gap:** `tests/integration/test_idempotency.py` are stubs — testcontainers not wired

---

### Phase 3 — Conversational graph & media understanding ✅ COMPLETE

- `build_conversational_graph()` factory — 7 nodes, conditional edges, `MemorySaver` checkpointer
- `build_production_graph(settings)` — builds graph with real LLM via `build_llm(settings)`
- Nodes: eligibility, media_understanding, context_builder, agent, tool_executor, guardrails, command_dispatch
- `MediaUnderstanding` subgraph — audio/image/doc with partial/unsupported/failed fallbacks
- `ConversationContext` builder — governed memory + published config + active tools only
- Stub LLM path for tests — no real LLM calls in test suite
- **Gate evidence:** `test_graph.py` (5 tests, stub LLM)

---

### Phase 4 — Agent Tools MCP & governed side effects ✅ COMPLETE

- `AgentToolRegistry` — 20 tools, only `active` tools visible to agent
- `AgentToolMcpGateway` — bridges agent ↔ registry
- `ToolPolicyGuard` — kanban `suggest_only`, campaigns `draft_only` by default (INV-06)
- `ToolExecutor` — read/draft/outbox routing; side-effecting tools → outbox only (INV-04)
- 9 catalog modules: scheduling, qualification, kanban, campaigns, followup, kb, voice, lead, history
- All catalog handlers hash sensitive content — never store raw text (INV-05)
- **Gate evidence:** `test_catalog_tools.py` (24 tests), `test_executor.py` (6 tests), `test_tools.py`

---

### Phase 5 — Campaigns & follow-up ✅ COMPLETE (within Phase 4)

- Campaign pipeline: create, validate_audience, request_approval, dispatch_batch, status, cancel
- Batch size capped at 100; template/name hashed (INV-05)
- Follow-up: message hashed, policy enforced
- All writes go through outbox (INV-04)

---

### Phase 6 — Memory, history, KB & config publishing ✅ COMPLETE

- `GovernedMemoryStore` — namespace, TTL, draft/publish lifecycle
- `HistoryAnalyzer` — read-only, redacted, produces `draft_ref` (never raw content)
- `KbSuggestHandler` — topic + content hashed, `draft_ref` returned (never direct publish)
- `ConfigPublisher` — draft → validate → review → publish; Postgres is canonical

---

### Phase 7 — Observability, LLM factory & hardening ✅ COMPLETE

- Structured logging with `RedactionProcessor` (all log fields pass redaction filter)
- `correlation_id` threading through all operations
- `iara/llm/factory.py` — `build_llm(settings)` with dual-provider support and automatic model family detection
- Worker signal handling: `loop.add_signal_handler()` + `call_soon_threadsafe()` (thread-safe)
- Webhook `_queue_processing_job()` fully implemented — publishes to RabbitMQ via `app.state.rabbitmq`
- Lifespan RabbitMQ connection with graceful degradation if broker is unavailable at startup
- Dev tenant auto-registration (`test_tenant_001` / `11111`) on `development` and `sandbox` envs
- Outbox drainer: adapter-None check moved to batch level (no silent per-command stalls)
- Job consumer: `tenant_id` / `conversation_id` validation before graph invocation
- Per-gate sanitized evidence: `docs/evidence/invariants_gate_report.md`
- `docs/runbook.md`, `docs/configuration.md`, `docs/secrets.md`, `docs/rollback.md`, `docs/der.md`
- **Gate evidence:** All 113 tests pass (`make test-unit && make test-security`)

---

### Phase 8 — Per-tenant MCP catalog & admin surface ⏸ NOT STARTED (scope-gated)

Blocked until contracted — see Open Decisions #8 and #13.

Deliverables when contracted:
- `tenant_mcp_servers` catalog (available → sandbox → active lifecycle)
- Custom client MCP onboarding pipeline (request → sandbox → discovery → risk → mapping → publish)
- Optional Chatwoot Dashboard App admin surface (iframe, no tokens, no production access)
- Agent Config Organizer as a separate admin graph

---

## 5. What remains before production

### 5.1 Required (blocking)

| Item | How | Notes |
|------|-----|-------|
| Configure real secrets | `.env` or secret manager | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, `DATABASE_URL`, `RABBITMQ_URL` |
| Set `IARA_PRODUCTION_AUTHORIZED=true` | `.env` | Guards all production writes (INV-07) |
| Create tenant records | DB | `INSERT INTO tenants ...` + `provider_accounts` |
| Run migrations against production DB | `make migrate` | Requires real Postgres |
| Register Chatwoot webhook URL | Chatwoot settings | `POST /webhooks/chatwoot/{tenant_key}` |
| Deploy API + Worker | Docker / K8s | Use `docker-compose --profile full up` or equivalent |

### 5.2 Recommended before production

| Item | Why |
|------|-----|
| Wire integration tests (`testcontainers`) | `tests/integration/test_idempotency.py` placeholders — G4 gate not fully closed |
| Review `docs/decisions/OPEN_DECISIONS.md` | 14 decisions still open; most critical: #1, #5, #6, #8, #13 |
| Confirm kanban/campaign modes per tenant | Defaults are safe (`suggest_only` / `draft_only`) but should be explicit per customer |
| Security review of `SENSITIVE_FIELDS` | Ensure all new data fields that transit the system are covered by redaction |
| Load test the webhook endpoint | Validate RabbitMQ backpressure and debounce behavior under volume |

---

## 6. Development workflow

```bash
uv sync --all-groups    # install all deps
cp .env.example .env    # configure environment
make up                 # start Postgres + RabbitMQ
make migrate            # apply schema

make format             # black + ruff --fix + flake8 (auto-fix)
make lint               # ruff + flake8 (read-only)
make type               # mypy strict
make test-unit          # 113 tests, ~3 s, no infra required
make test-security      # invariant enforcement tests
make check              # format + lint + type + test-unit (CI gate)

make run                # uvicorn API server (port 8000)
make worker             # background workers
make ui                 # Streamlit test UI (port 8501)
```

---

## 7. Gate summary

| Gate | Phase | Status | Evidence |
|------|-------|--------|---------|
| G0/G1 | Foundations + contracts | ✅ Green | `test_contracts.py`, `test_redaction.py` |
| G2 | Provider layer + MCP | ✅ Green | `test_cross_tenant.py`, `test_fail_closed.py` |
| G3/G4 | Persistence + queues | ✅ Green (unit) | `test_messaging.py`; integration stubs pending |
| G5 | Agent Tools + side effects | ✅ Green | `test_catalog_tools.py`, `test_executor.py`, `test_tools.py` |
| G6 | Observability + pilot | ✅ Green | `docs/evidence/invariants_gate_report.md` |
| G7 | LLM factory + hardening | ✅ Green | All 113 tests pass; bugs resolved |
| G8 (integration) | Testcontainers | ⏸ Stubs | `tests/integration/test_idempotency.py` — not wired |
| G9 (MCP catalog) | Phase 8 | ⏸ Not started | Scope-gated — Open Decisions #8, #13 |

---

## 8. Open decisions

Tracked in `docs/decisions/OPEN_DECISIONS.md`. Critical before production:

| # | Decision | Default (safe) |
|---|----------|----------------|
| 1 | Which features are in mandatory first delivery | Phases 0–7 |
| 5 | Initial kanban mode | `suggest_only` |
| 6 | Initial campaign mode | `draft_only` |
| 8 | Who maintains `tenant_mcp_servers` | Blocks Phase 8 |
| 13 | Dashboard / config organizer in first delivery | Blocks Phase 8 |

---

## 9. Anti-patterns (never ship code that does these)

- Passing raw Chatwoot MCP catalog or tool names to the LLM
- Executing a side effect directly inside a replayable LangGraph node (use the outbox)
- Any permissive fallback on tenant / account / inbox / capability ambiguity
- Secrets, tokens, real phone numbers, raw payloads, base64 blobs, full contact lists, or raw conversation content in logs, audit events, or evidence
- A campaign real-send path without consent/opt-out, rate limit, approval, per-recipient outbox, idempotency, and readback
- Any code path reaching a real production tenant without `IARA_PRODUCTION_AUTHORIZED=true`
- Skipping the cross-tenant account verification before any provider call
