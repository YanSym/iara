# IAra — Configuration Reference

All configuration is driven by environment variables, loaded via `pydantic-settings`.
Copy `.env.example` to `.env` and fill in values.

## Core Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_ENV` | `development` | Runtime environment (`development`, `staging`, `production`) |
| `IARA_PRODUCTION_AUTHORIZED` | `false` | Must be `true` to unlock production writes (INV-07) |
| `DATABASE_URL` | `postgresql+asyncpg://iara:iara_dev@localhost:5432/iara_dev` | Async Postgres DSN |
| `RABBITMQ_URL` | `amqp://iara:iara_dev@localhost:5672/iara` | AMQP URL |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | `console` | Log format (`console` for dev, `json` for prod) |

## LLM / Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY_REF` | `secret://anthropic/api_key` | Secret ref for Anthropic API key |
| `IARA_DEFAULT_LLM_MODEL` | `claude-sonnet-4-6` | Default Claude model ID |
| `IARA_LLM_TEMPERATURE` | `0.3` | LLM sampling temperature |
| `IARA_LLM_MAX_TOKENS` | `4096` | Maximum response tokens |

## Chatwoot MCP

| Variable | Default | Description |
|----------|---------|-------------|
| `CHATWOOT_MCP_BASE_URL` | — | Chatwoot instance base URL (no trailing slash) |
| `CHATWOOT_MCP_CREDENTIAL_REF` | `secret://chatwoot/mcp_token` | Secret ref for Chatwoot `Api-Access-Token` |
| `CHATWOOT_MCP_API_TOKEN_REF` | `secret://chatwoot/api_token` | Alternate secret ref (legacy alias) |
| `CHATWOOT_ACCOUNT_ID` | — | Default Chatwoot account ID (numeric string, e.g. `59`) |
| `CHATWOOT_MCP_SLUG` | — | MCP server slug (per-tenant, e.g. `mcp-suporte`) |

The MCP endpoint is constructed as `{CHATWOOT_MCP_BASE_URL}/mcp/{CHATWOOT_ACCOUNT_ID}/{CHATWOOT_MCP_SLUG}`.
The `Api-Access-Token` header is used — not `Bearer`.

## Scheduling / Integrations

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CALENDAR_MCP_ENDPOINT` | — | Google Calendar MCP server endpoint |
| `GOOGLE_CALENDAR_CREDENTIALS_REF` | `secret://google/calendar_creds` | Secret ref |
| `CLINICORP_MCP_ENDPOINT` | — | ClinicOrp MCP server endpoint |
| `CLINICORP_API_KEY_REF` | `secret://clinicorp/api_key` | Secret ref |

## Operational Knobs

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_DEBOUNCE_WINDOW_SECONDS` | `10` | Time window for conversation debounce |
| `IARA_LEASE_TTL_SECONDS` | `300` | Conversation run lease TTL |
| `IARA_MAX_CONTENT_LENGTH` | `4096` | Max message content length in chars |
| `RABBITMQ_PREFETCH_COUNT` | `10` | Consumer prefetch count |

## Pilot / Seeding

These variables are used by `make seed-pilot` / `src/iara/persistence/seeds/seed_pilot.py`
to bootstrap the first tenant in a new environment. Not required at runtime after seeding.

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_PILOT_TENANT_ID` | — | UUID to assign to the pilot tenant |
| `IARA_PILOT_WEBHOOK_KEY` | — | Raw webhook key (stored as SHA-256 hash in DB) |
| `IARA_PILOT_TENANT_NAME` | `IAra Pilot` | Display name for the pilot tenant |

The pilot seed also reads `CHATWOOT_ACCOUNT_ID` and `CHATWOOT_MCP_SLUG` to create the
provider account binding.

## Policy / Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_KANBAN_DEFAULT_MODE` | `suggest_only` | Kanban write mode (`suggest_only`, `write_sandbox`, `write_confirmed`) |
| `IARA_CAMPAIGN_DEFAULT_MODE` | `draft_only` | Campaign write mode (`draft_only`, `dry_run`, `sandbox`, `approved_send`) |

## Memory / Context

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_MEMORY_BACKEND` | `in_memory` | Memory backend (`in_memory`, `postgres`) |
| `IARA_MAX_CONTEXT_MESSAGES` | `20` | Max messages loaded into agent context |
| `IARA_MAX_CONTEXT_TOKENS` | `16000` | Max context tokens before truncation |

## Secret Refs

Secrets are never stored inline. The `*_ref` fields point to a secret store path:

```
secret://anthropic/api_key   → fetched at runtime from the configured secret store
```

In development, set the actual value directly:

```bash
ANTHROPIC_API_KEY_REF=sk-ant-...   # only in .env — never committed
```

In production, configure a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.)
and ensure the runtime can resolve `secret://` URIs.
