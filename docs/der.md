# IAra — Data Entity Relationships

## Core Tables

```
tenants (1)
      │
      ├─▶ provider_accounts (N)        — provider/account bindings per tenant
      ├─▶ event_receipts (N)           — idempotency per tenant
      ├─▶ conversation_debounce (N)    — debounce per tenant+conversation
      ├─▶ conversation_run_leases (N)  — fencing per tenant+conversation
      ├─▶ provider_command_outbox (N)  — outbound commands per tenant
      ├─▶ follow_up_queue (N)          — scheduled follow-up messages per tenant
      ├─▶ hitl_holds (N)               — HITL pause records per tenant
      ├─▶ agent_config_versions (N)    — config drafts per tenant
      ├─▶ config_publications (N)      — published config snapshots per tenant
      ├─▶ runtime_run_steps (N)        — audit of graph executions
      └─▶ safe_audit_events (N)        — sanitized audit trail
```

## Table Definitions (abbreviated)

### `tenant_agent_configs`
```sql
id                UUID PRIMARY KEY
tenant_key        TEXT UNIQUE NOT NULL         -- webhook path segment
tenant_id         UUID UNIQUE NOT NULL         -- internal identifier
provider_account_id TEXT NOT NULL              -- bound Chatwoot account
status            TEXT NOT NULL DEFAULT 'active'
config_json       JSONB NOT NULL DEFAULT '{}'
version           INTEGER NOT NULL DEFAULT 1
created_at        TIMESTAMPTZ NOT NULL
updated_at        TIMESTAMPTZ NOT NULL
```

### `event_receipts`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL REFERENCES tenant_agent_configs(tenant_id)
idempotency_key   TEXT NOT NULL
received_at       TIMESTAMPTZ NOT NULL
UNIQUE (tenant_id, idempotency_key)
```

### `conversation_debounce`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL
conversation_id   TEXT NOT NULL
debounce_until    TIMESTAMPTZ NOT NULL
UNIQUE (tenant_id, conversation_id)
```

### `conversation_run_leases`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL
conversation_id   TEXT NOT NULL
fencing_token     UUID NOT NULL DEFAULT gen_random_uuid()
acquired_at       TIMESTAMPTZ NOT NULL
expires_at        TIMESTAMPTZ NOT NULL
released_at       TIMESTAMPTZ
UNIQUE (tenant_id, conversation_id)
```

### `provider_command_outbox`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL
command_id        UUID UNIQUE NOT NULL
idempotency_key   TEXT NOT NULL
correlation_id    TEXT NOT NULL
provider          TEXT NOT NULL
capability_name   TEXT NOT NULL
parameters_json   JSONB NOT NULL
risk_class        TEXT NOT NULL
status            TEXT NOT NULL DEFAULT 'pending'
retry_count       INTEGER NOT NULL DEFAULT 0
failure_reason    TEXT
scheduled_at      TIMESTAMPTZ NOT NULL
sent_at           TIMESTAMPTZ
confirmed_at      TIMESTAMPTZ
failed_at         TIMESTAMPTZ
UNIQUE (idempotency_key)  -- uq_outbox_idempotency
```

### `tenant_kb_entries`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL REFERENCES tenant_agent_configs(tenant_id)
entry_key         TEXT NOT NULL
title_ref         TEXT NOT NULL               -- opaque hash
content_ref       TEXT NOT NULL               -- opaque hash
status            TEXT NOT NULL DEFAULT 'draft'
version           INTEGER NOT NULL DEFAULT 1
published_at      TIMESTAMPTZ
UNIQUE (tenant_id, entry_key)
```

### `audit_events`
```sql
id                UUID PRIMARY KEY
tenant_ref        TEXT NOT NULL               -- opaque hash of tenant_id
conversation_ref  TEXT NOT NULL               -- opaque hash
event_type        TEXT NOT NULL
actor_ref         TEXT NOT NULL               -- opaque hash
resource_ref      TEXT NOT NULL               -- opaque hash
outcome           TEXT NOT NULL
evidence_json     JSONB NOT NULL              -- counts/hashes only, no PII
created_at        TIMESTAMPTZ NOT NULL
```

### `hitl_holds`
```sql
id                UUID PRIMARY KEY
run_id            TEXT(128) UNIQUE NOT NULL        -- LangGraph run identifier
tenant_id         UUID NOT NULL REFERENCES tenants(id)
conversation_id   TEXT(256) NOT NULL
thread_id         TEXT(256) NOT NULL               -- LangGraph checkpointer key
reason            TEXT                             -- sanitized hold reason
status            TEXT(32) NOT NULL DEFAULT 'pending'
                  -- 'pending' | 'approved' | 'rejected'
resolved_by       TEXT(256)                        -- opaque approver reference
context_snapshot  JSONB                            -- non-sensitive graph snapshot
requested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
resolved_at       TIMESTAMPTZ
INDEX ix_hitl_holds_tenant_status (tenant_id, status)
INDEX ix_hitl_holds_run_id_idx (run_id)
```

### `follow_up_queue`
```sql
id                UUID PRIMARY KEY
tenant_id         UUID NOT NULL REFERENCES tenants(id)
conversation_id   TEXT(256) NOT NULL
contact_ref       TEXT(256) NOT NULL               -- SHA-256 hash of contact id
message_ref       TEXT(256) NOT NULL               -- SHA-256 hash of message text
message_length    INTEGER NOT NULL                 -- character count (no raw text)
reason_ref        TEXT(256) NOT NULL               -- SHA-256 hash of reason
trigger_at        TIMESTAMPTZ NOT NULL             -- when to send
status            TEXT(32) NOT NULL DEFAULT 'pending'
                  -- 'pending' | 'sent' | 'skipped' | 'failed'
attempt_count     INTEGER NOT NULL DEFAULT 0
max_attempts      INTEGER NOT NULL DEFAULT 3
opted_out         BOOLEAN NOT NULL DEFAULT FALSE
correlation_id    TEXT(128) NOT NULL
idempotency_key   TEXT(512) NOT NULL
created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
sent_at           TIMESTAMPTZ
skip_reason       TEXT(256)                        -- reason code for skipped/failed
UNIQUE uq_follow_up_idempotency (idempotency_key)
INDEX ix_follow_up_trigger (trigger_at) WHERE status='pending'
INDEX ix_follow_up_tenant_status (tenant_id, status)
```

## Invariants Enforced at Schema Level

| Invariant | Mechanism |
|-----------|-----------|
| No duplicate events per tenant | `UNIQUE (tenant_id, idempotency_key)` on `event_receipts` |
| One active run per conversation | `UNIQUE (tenant_id, conversation_id)` on `conversation_run_leases` |
| Outbox idempotency | `UNIQUE (idempotency_key)` on `provider_command_outbox` |
| Follow-up idempotency | `UNIQUE uq_follow_up_idempotency (idempotency_key)` on `follow_up_queue` |
| HITL run uniqueness | `UNIQUE (run_id)` on `hitl_holds` — one hold per LangGraph run |
| No raw PII in follow-ups | `message_ref`, `contact_ref`, `reason_ref` store SHA-256 hashes only |
| No raw PII in HITL holds | `context_snapshot` constrained by application to non-sensitive fields only |
| No raw PII in audit | `evidence_json` constrained by application to counts/hashes only |
