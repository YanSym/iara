# IAra — Data Entity Relationships

## Core Tables

```
tenant_agent_configs (1)
      │
      ├─▶ event_receipts (N)           — idempotency per tenant
      ├─▶ conversation_debounce (N)    — debounce per tenant+conversation
      ├─▶ conversation_run_leases (N)  — fencing per tenant+conversation
      ├─▶ provider_command_outbox (N)  — outbound commands per tenant
      ├─▶ runtime_run_steps (N)        — audit of graph executions
      ├─▶ tenant_kb_entries (N)        — KB entries per tenant
      └─▶ audit_events (N)             — sanitized audit trail
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

## Invariants Enforced at Schema Level

| Invariant | Mechanism |
|-----------|-----------|
| No duplicate events per tenant | `UNIQUE (tenant_id, idempotency_key)` on `event_receipts` |
| One active run per conversation | `UNIQUE (tenant_id, conversation_id)` on `conversation_run_leases` |
| Outbox idempotency | `UNIQUE (idempotency_key)` on `provider_command_outbox` |
| No raw PII in KB | `title_ref` / `content_ref` fields store hashes, not content |
| No raw PII in audit | `evidence_json` constrained by application to counts/hashes only |
