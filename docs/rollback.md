# IAra — Rollback Procedures

## Database Migration Rollback

Alembic supports `downgrade`. Each migration file includes a `downgrade()` function.

### Roll Back One Migration
```bash
alembic downgrade -1
```

### Roll Back to a Specific Revision
```bash
alembic history                      # list revisions
alembic downgrade <revision_id>      # downgrade to that revision
```

### Roll Back to Empty Database
```bash
alembic downgrade base
```

**Warning**: `downgrade base` drops all application tables. Run only in development
unless you have a verified backup.

---

## Config Publication Rollback

IAra supports rolling back tenant configuration to a previous publication without
redeploying the application. The `config_publications` table retains all past
publications; rollback re-activates a previous one.

### Roll Back via API
```bash
POST /config/{tenant_id}/rollback/{publication_id}
```

Example:
```bash
curl -X POST http://localhost:8000/config/<tenant-uuid>/rollback/<publication-uuid> \
  -H "Content-Type: application/json"
# → {"publication_id": "<uuid>", "is_active": true, ...}
```

This call:
1. Sets the specified publication's `is_active = true`.
2. Deactivates all other publications for that tenant.
3. The new active publication takes effect on the next graph invocation for that tenant.

### Find Available Publications to Roll Back To
```sql
SELECT publication_id, published_by, published_at, is_active
FROM config_publications
WHERE tenant_id = '<tenant-uuid>'
ORDER BY published_at DESC
LIMIT 10;
```

---

## Application Rollback

### Docker Compose (local)
```bash
# Roll back to previous image tag
docker-compose pull --no-parallel api worker
docker-compose up -d api worker
```

### Production (kubernetes / ECS)
Roll back the deployment to the previous task definition or image tag
using your orchestrator's rollback command. The application is stateless
(all state is in Postgres and RabbitMQ) so rolling back the image is safe.

---

## Data Recovery

### Pending Outbox Commands After Rollback

If a deployment was rolled back mid-drain, some outbox commands may be in
`sent` status without a corresponding `confirmed`. Safe to re-confirm manually:

```sql
-- Find stuck commands sent before rollback
SELECT command_id, capability_name, sent_at
FROM provider_command_outbox
WHERE status = 'sent'
  AND sent_at < NOW() - INTERVAL '10 minutes';

-- After verifying they were actually executed in the provider, confirm them:
UPDATE provider_command_outbox
SET status = 'confirmed', confirmed_at = NOW()
WHERE command_id = '<uuid>';
```

### Idempotency Replay Protection

The `event_receipts` table prevents re-processing events even after rollback.
If you need to re-process a specific event:

```sql
DELETE FROM event_receipts
WHERE idempotency_key = '<key>'
  AND tenant_id = '<uuid>';
```

Re-process by re-sending the Chatwoot webhook or enqueueing the job manually.

---

## Follow-Up Queue Drain Procedure

Use this procedure before a maintenance window or after a rollback to safely
drain pending follow-up items so they are not silently lost.

### 1. Check Current Queue Size
```sql
SELECT status, COUNT(*) FROM follow_up_queue GROUP BY status;
```

### 2. Pause the Scheduler
Stop or scale down the worker that runs `FollowUpSchedulerWorker`. The
scheduler shares the worker process with `JobConsumerWorker` and `OutboxDrainerWorker`,
so draining requires stopping the whole worker and processing items manually if needed.

### 3. Inspect Pending Items
```sql
SELECT id, tenant_id, conversation_id, trigger_at, attempt_count
FROM follow_up_queue
WHERE status = 'pending'
ORDER BY trigger_at ASC;
```

### 4. Decide: skip or promote
To skip all pending follow-ups before a window:
```sql
UPDATE follow_up_queue
SET status = 'skipped', skip_reason = 'maintenance_window', updated_at = NOW()
WHERE status = 'pending';
```

To allow them to send after the window, simply restart the worker — the scheduler
will pick up all items whose `trigger_at` has passed.

### 5. Verify Drain Complete
```sql
SELECT COUNT(*) FROM follow_up_queue WHERE status = 'pending';
-- Should be 0 if drain is complete.
```

---

## Safe Deployment Windows

Deployments that only change code (no schema changes) can be rolled back at
any time without data loss.

Deployments that add new columns or tables: safe to roll back (columns are
additive; rollback drops them but existing data is unaffected).

Deployments that drop columns or tables: coordinate with team; rollback may
not be possible without restoring from backup.
