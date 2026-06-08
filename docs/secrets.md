# IAra — Secrets Management

## Principle

No secret is stored in the database, in log output, in the outbox, or in any
durable storage. All credentials are accessed at runtime via secret references.

## Secret Reference Format

```
secret://<namespace>/<key>
```

Examples:
- `secret://anthropic/api_key`
- `secret://chatwoot/api_token`
- `secret://google/calendar_creds`

## Local Development

In `.env`, write the actual value in the `*_ref` field:

```bash
ANTHROPIC_API_KEY_REF=sk-ant-api03-...
IARA_WEBHOOK_SECRET_REF=dev_webhook_secret_here
```

The settings loader detects that the value does not start with `secret://`
and uses it as-is (only in non-production environments).

## Production

In `IARA_ENV=production`, all `*_ref` fields that start with `secret://` are
resolved via the configured secret store. The runtime must have IAM or
equivalent permissions to read from the store.

Supported backends (extend `src/iara/config/settings.py` to add more):
- AWS Secrets Manager (`secret://aws/<secret-name>`)
- HashiCorp Vault (`secret://vault/<path>`)

## What Must Never Be Committed

- `.env` (covered by `.gitignore`)
- Any file with real API keys, tokens, or passwords
- Any migration that stores credentials

## Redaction in Logs

`RedactionProcessor` (see `src/iara/security/redaction.py`) strips sensitive
fields from every log event. Patterns covered:

- Anthropic API key (`sk-ant-*`)
- Brazilian phone numbers (`+55...`)
- CPF numbers (`\d{3}\.\d{3}\.\d{3}-\d{2}`)
- Pre-signed URLs (`X-Amz-Signature=...`)
- Base64 blobs longer than 100 characters
- Field names: `token`, `api_key`, `password`, `secret`, `authorization`, etc.

## Audit Trail

The `audit_events` table stores only:
- SHA-256 hashes of identifiers
- Opaque references
- Counts and statuses

Raw PII and credentials are never written to the audit trail (INV-05).
