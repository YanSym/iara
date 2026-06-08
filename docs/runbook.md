# IAra — Runbook

## Health Checks

### API
```bash
curl http://localhost:8000/health
# → {"status": "ok", "env": "development"}
```

### Worker
```bash
# Worker logs to stdout — look for:
# {"event": "job_consumer_ready", ...}
# {"event": "outbox_drainer_ready", ...}
```

### RabbitMQ Management UI
```
http://localhost:15672   user: iara   password: iara_dev
```

### Postgres
```bash
psql postgresql://iara:iara_dev@localhost:5432/iara_dev
\dt   -- lists all tables
```

---

## Common Operations

### Run Migrations
```bash
make migrate
# or manually:
alembic -c alembic.ini upgrade head
```

### Run Format + Lint + Type Check
```bash
make check
```

### Run All Tests
```bash
make test
```

### Restart Infrastructure
```bash
make down && make up
```

### View Pending Outbox Commands
```sql
SELECT command_id, capability_name, status, retry_count, scheduled_at
FROM provider_command_outbox
WHERE status IN ('pending', 'sent', 'failed')
ORDER BY scheduled_at DESC
LIMIT 50;
```

### View Dead-Lettered Commands
```sql
SELECT command_id, capability_name, failure_reason, retry_count, failed_at
FROM provider_command_outbox
WHERE status = 'dead_lettered'
ORDER BY failed_at DESC;
```

### Manually Retry a Dead-Lettered Command
```sql
UPDATE provider_command_outbox
SET status = 'pending', retry_count = 0, failure_reason = NULL
WHERE command_id = '<command-uuid>';
```

### View Active Leases
```sql
SELECT tenant_id, conversation_id, fencing_token, acquired_at, expires_at
FROM conversation_run_leases
WHERE expires_at > NOW();
```

### Force-Release a Stuck Lease
```sql
DELETE FROM conversation_run_leases
WHERE conversation_id = '<conversation-id>'
  AND expires_at < NOW() + INTERVAL '5 minutes';
```

---

## Incident Response

### High DLX (Dead-Lettered) Message Rate

1. Check outbox: `SELECT COUNT(*) FROM provider_command_outbox WHERE status='dead_lettered';`
2. Inspect failure reasons: `SELECT DISTINCT failure_reason FROM provider_command_outbox WHERE status='dead_lettered';`
3. If provider is down: pause the drainer, fix the provider, retry.
4. If capability name is wrong: fix the ChatwootMcpRegistry, redeploy, retry manually.

### Lease Conflicts (LeaseConflictError spamming logs)

1. Check for stuck leases: `SELECT * FROM conversation_run_leases WHERE expires_at < NOW();`
2. Expired leases are normally auto-expired by TTL. If not, force-release as above.
3. Check for duplicate job consumers (only one consumer per conversation should run).

### Cross-Tenant Rejection Spike

`CrossTenantError` in logs means a webhook arrived with an account_id that doesn't
match the tenant binding. Possible causes:
- Chatwoot misconfiguration (wrong account ID in webhook URL)
- Tenant record misconfigured in the database

Check: `SELECT tenant_key, provider_account_id FROM tenant_agent_configs WHERE tenant_key='<key>';`

---

## Deployment Checklist

- [ ] `IARA_ENV=production` set
- [ ] `IARA_PRODUCTION_AUTHORIZED=true` set (required to unlock writes — INV-07)
- [ ] All `*_ref` fields resolve correctly from the secret store
- [ ] Migrations applied: `alembic upgrade head`
- [ ] RabbitMQ topology declared (happens automatically on worker start)
- [ ] At least one worker instance running
- [ ] Health check endpoint returns 200
- [ ] RabbitMQ DLX queues monitored for dead-lettered messages
