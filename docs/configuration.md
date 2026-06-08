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
| `CHATWOOT_MCP_BASE_URL` | — | Chatwoot instance base URL |
| `CHATWOOT_MCP_API_TOKEN_REF` | `secret://chatwoot/api_token` | Secret ref for Chatwoot API token |
| `CHATWOOT_MCP_ACCOUNT_ID` | — | Default Chatwoot account ID |

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

## Policy / Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `IARA_KANBAN_MODE` | `suggest_only` | Kanban write mode (`suggest_only`, `write`) |
| `IARA_CAMPAIGN_MODE` | `draft_only` | Campaign write mode (`draft_only`, `approved_send`) |

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
