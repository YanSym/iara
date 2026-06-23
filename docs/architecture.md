# IAra — Architecture Reference

## System Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ External                                                                     │
│  Chatwoot ──webhook──▶ FastAPI /webhooks/{tenant_key}                        │
└──────────────────────────────────────────────────────────────────────────────
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ API Layer                                                                    │
│  TenantResolver → ChatwootEventNormalizer → EligibilityChecker               │
│  ↓ accepted                                                                  │
│  MessagePublisher → RabbitMQ [iara.jobs.conversation]                        │
└──────────────────────────────────────────────────────────────────────────────
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Worker — Job Consumer                                                        │
│  LeaseRepository.acquire() → LangGraph.ainvoke()                             │
│                                                                              │
│  Graph Nodes:                                                                │
│   eligibility → media_understanding → context_builder                        │
│              → agent ←→ tool_executor (loop)                                 │
│              → guardrails → hitl_node → command_dispatch                     │
│                                                                              │
│  command_dispatch: enqueues to provider_command_outbox (Postgres)            │
│  hitl_node: writes HitlHoldRecord → suspends run → resumes on approve        │
└──────────────────────────────────────────────────────────────────────────────
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Worker — Outbox Drainer                                                      │
│  poll provider_command_outbox → execute via ChatwootMcpAdapter               │
│  → ReadbackService.confirm_*() → mark_confirmed / mark_failed                │
└──────────────────────────────────────────────────────────────────────────────
          │ (follow-up items written to follow_up_queue by command_dispatch)
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Worker — Follow-Up Scheduler                                                 │
│  poll follow_up_queue WHERE status='pending' AND trigger_at <= now           │
│  → increment attempt_count → enqueue to provider_command_outbox              │
│  → OutboxDrainerWorker delivers followup_reengage_conversation               │
│  Respects: opted_out flag, max_attempts, idempotency_key                     │
└──────────────────────────────────────────────────────────────────────────────
```

## MCP Layers

Two MCP layers are present. They are explicitly decoupled:

| Layer | Who uses it | What it exposes |
|-------|-------------|-----------------|
| Operational Chatwoot MCP | Outbox Drainer only | Raw Chatwoot API tool calls |
| Agent Tools MCP | LLM / LangGraph agent node | Logical business capability names |

The LLM never sees the Chatwoot MCP tool names (INV-03).

### Chatwoot MCP Endpoint

```
POST {base_url}/mcp/{account_id}/{slug}
Header: Api-Access-Token: <token>          (not Bearer)
Transport: HTTP JSON-RPC 2.0 — method "tools/call"
```

`base_url` is set via `CHATWOOT_MCP_BASE_URL`. The `account_id` and `slug` are
resolved per-tenant from `CHATWOOT_ACCOUNT_ID` / `CHATWOOT_MCP_SLUG` (defaults)
or overridden per provider account record in the `provider_accounts` table.

## Idempotency Architecture

```
Event arrives
      │
      ▼ (normalize + SHA-256 hash of raw bytes)
RawEventRef stored in NormalizedChatwootEvent
      │
      ▼
EligibilityChecker.check()
   ├─ IdempotencyRepository.is_duplicate(tenant_id, key) → True  → reject
   ├─ DebounceRepository.is_debouncing(conversation_id) → True  → reject
   └─ All pass → publish job
                        │
                        ▼
              LeaseRepository.acquire(conversation_id)  → conflict → nack
                        │ success
                        ▼
              LangGraph.ainvoke()
                        │ command_dispatch
                        ▼
              OutboxRepository.enqueue()  ← ON CONFLICT DO NOTHING
                        │
                        ▼ (async, separate drainer)
              Provider.execute_command()
                        │
                        ▼
              ReadbackService.confirm_*()
                        │
                        ▼
              OutboxRepository.mark_confirmed()
```

## Database Schema (summary)

| Table | Purpose |
|-------|---------|
| `tenants` | Tenant registry (tenant_key, provider_account_id) |
| `provider_accounts` | Provider account bindings per tenant |
| `event_receipts` | Idempotency ledger (unique idempotency key per tenant) |
| `conversation_debounce` | Prevents rapid re-processing of same conversation |
| `conversation_run_leases` | Fencing tokens — one active run per conversation |
| `provider_command_outbox` | Pending/sent/confirmed/failed provider commands |
| `follow_up_queue` | Durable queue of scheduled follow-up messages (see FollowUpSchedulerWorker) |
| `hitl_holds` | HITL pause records awaiting human approval (see HitlHoldRepository) |
| `runtime_run_steps` | Audit log of LangGraph node executions |
| `agent_config_versions` | Per-tenant config drafts |
| `config_publications` | Published config snapshots (draft→publish→rollback pipeline) |
| `safe_audit_events` | Sanitized audit trail (hashes, refs, counts only) |

### Persistence Classes

| Class | Module | Responsibility |
|-------|--------|----------------|
| `PostgresTenantRepository` | `iara.tenancy.postgres_repository` | Resolve tenant by key; cache TTL |
| `HitlHoldRepository` | `iara.persistence.repositories.hitl_holds` | Register / approve / reject HITL holds |
| `FollowUpRepository` | `iara.persistence.repositories.follow_up` | Enqueue, fetch_due, mark_sent/skipped/failed |
| `OutboxRepository` | `iara.persistence.repositories.outbox` | Enqueue provider commands; update status |
| `LeaseRepository` | `iara.persistence.repositories.leases` | Acquire/release conversation fencing leases |

## Security Boundaries

```
[Tenant A] ──┐
             ├── TenantContext.verify_provider_account() re-checked before each side effect
[Tenant B] ──┘

CrossTenantError raised immediately if account_id does not match tenant binding.
FailClosedError raised if any ambiguity cannot be resolved.
```
