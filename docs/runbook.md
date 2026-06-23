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
# {"event": "follow_up_scheduler_ready", ...}
```

### Follow-Up Queue
```sql
-- Pending follow-ups (should drain over time)
SELECT status, COUNT(*) FROM follow_up_queue GROUP BY status ORDER BY status;

-- Overdue items (trigger_at passed but still pending — scheduler may be stuck)
SELECT id, conversation_id, trigger_at, attempt_count, max_attempts
FROM follow_up_queue
WHERE status = 'pending'
  AND trigger_at < NOW() - INTERVAL '5 minutes'
ORDER BY trigger_at ASC
LIMIT 20;

-- Failed items
SELECT id, conversation_id, attempt_count, skip_reason
FROM follow_up_queue
WHERE status = 'failed'
ORDER BY updated_at DESC
LIMIT 20;
```

### HITL Holds
```sql
-- Pending holds awaiting human approval
SELECT run_id, tenant_id, conversation_id, reason, requested_at
FROM hitl_holds
WHERE status = 'pending'
ORDER BY requested_at ASC;
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

## FollowUpSchedulerWorker Operations

### View Follow-Up Scheduler Status
The scheduler logs `follow_up_scheduler_ready` on start and `follow_up_scheduler_batch`
each time it processes items. A silent scheduler (no batch logs) with overdue items
indicates the worker process is not running.

### Drain the Follow-Up Queue Manually
If the scheduler is stuck and you need to manually move items to the outbox:
```sql
-- Inspect what is overdue
SELECT id, tenant_id, conversation_id, trigger_at, attempt_count, max_attempts
FROM follow_up_queue
WHERE status = 'pending' AND trigger_at < NOW()
ORDER BY trigger_at ASC;
```
Then restart the `FollowUpSchedulerWorker` (it runs in the same worker process as
`JobConsumerWorker` and `OutboxDrainerWorker`). The scheduler will pick up overdue
items on the next poll (every 30 seconds by default).

### Reset a Stuck Follow-Up Item
```sql
-- If an item is stuck in a non-terminal state, reset it:
UPDATE follow_up_queue
SET status = 'pending', attempt_count = 0, skip_reason = NULL, updated_at = NOW()
WHERE id = '<item-uuid>';
```

### Opt Out a Conversation from Follow-Ups
```sql
-- Mark all pending follow-ups for a conversation as opted_out:
UPDATE follow_up_queue
SET opted_out = TRUE, updated_at = NOW()
WHERE conversation_id = '<conversation-id>'
  AND tenant_id = '<tenant-uuid>'
  AND status = 'pending';
```

---

## HITL Hold Resolution Workflow

When a high-risk agent action triggers a HITL hold, the graph is suspended and
a `HitlHoldRecord` is written with `status='pending'`. The operator must resolve
it via the API.

### List Pending Holds
```bash
curl -s http://localhost:8000/hitl/pending | jq .
```

### Approve a Hold
```bash
curl -X POST http://localhost:8000/hitl/<run_id>/approve \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "operator@example.com"}'
```
This sets `status='approved'`, records `resolved_by` / `resolved_at`, and resumes
the LangGraph run from the `hitl_node` checkpoint.

### Reject a Hold
```bash
curl -X POST http://localhost:8000/hitl/<run_id>/reject \
  -H "Content-Type: application/json" \
  -d '{"rejected_by": "operator@example.com", "reason": "Not authorized"}'
```
This sets `status='rejected'` and terminates the run without executing the
pending provider commands.

### Stale Holds (no resolution after N hours)
```sql
SELECT run_id, conversation_id, reason, requested_at,
       EXTRACT(EPOCH FROM (NOW() - requested_at))/3600 AS age_hours
FROM hitl_holds
WHERE status = 'pending'
  AND requested_at < NOW() - INTERVAL '2 hours'
ORDER BY requested_at ASC;
```
Stale holds should be resolved or rejected to unblock the conversation.

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
