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

## Safe Deployment Windows

Deployments that only change code (no schema changes) can be rolled back at
any time without data loss.

Deployments that add new columns or tables: safe to roll back (columns are
additive; rollback drops them but existing data is unaffected).

Deployments that drop columns or tables: coordinate with team; rollback may
not be possible without restoring from backup.
