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
│              → guardrails → command_dispatch                                  │
│                                                                              │
│  command_dispatch: enqueues to provider_command_outbox (Postgres)            │
└──────────────────────────────────────────────────────────────────────────────
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Worker — Outbox Drainer                                                      │
│  poll provider_command_outbox → execute via ChatwootMcpAdapter               │
│  → ReadbackService.confirm_*() → mark_confirmed / mark_failed                │
└──────────────────────────────────────────────────────────────────────────────
```

## MCP Layers

Two MCP layers are present. They are explicitly decoupled:

| Layer | Who uses it | What it exposes |
|-------|-------------|-----------------|
| Operational Chatwoot MCP | Outbox Drainer only | Raw Chatwoot API tool calls |
| Agent Tools MCP | LLM / LangGraph agent node | Logical business capability names |

The LLM never sees the Chatwoot MCP tool names (INV-03).

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
| `event_receipts` | Idempotency ledger (unique idempotency key per tenant) |
| `conversation_debounce` | Prevents rapid re-processing of same conversation |
| `conversation_run_leases` | Fencing tokens — one active run per conversation |
| `provider_command_outbox` | Pending/sent/confirmed/failed provider commands |
| `runtime_run_steps` | Audit log of LangGraph node executions |
| `tenant_agent_configs` | Per-tenant configuration (draft→validate→publish) |
| `tenant_kb_entries` | Knowledge base entries (draft→published lifecycle) |
| `audit_events` | Sanitized audit trail (hashes, refs, counts only) |

## Security Boundaries

```
[Tenant A] ──┐
             ├── TenantContext.verify_provider_account() re-checked before each side effect
[Tenant B] ──┘

CrossTenantError raised immediately if account_id does not match tenant binding.
FailClosedError raised if any ambiguity cannot be resolved.
```
