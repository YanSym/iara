# IAra — Gate Acceptance Checklists (G0–G6)

Each gate must be signed off before proceeding to the next phase.
A gate is rejected if any mandatory item is unresolved or fail-closed behavior
cannot be demonstrated. Common rejection reasons are listed per gate.

---

## G0 — Kickoff & Architecture Decision

### Mandatory Artefacts
- [ ] G0 decision document (`docs/g0_kickoff_template.md`) signed by all parties
- [ ] Scheduling provider chosen and recorded (GCal / Clinicorp / Null)
- [ ] Kanban initial mode recorded (`suggest_only` | `write_sandbox` | `write_confirmed`)
- [ ] Follow-up mode recorded (`disabled` | `enabled_sandbox` | `enabled_production`)
- [ ] Pilot tenant list with HITL approvers named
- [ ] Sandbox environment confirmed available
- [ ] Non-scope confirmation per cl. 14.2 signed

### Evidence to Produce
- G0 decision document with all sections complete and signatures
- Email or written confirmation from Digi2b TI representative (Breno Cocheto, breno@digi2b.com)
- List of pilot tenant IDs and webhook keys in secure vault (not committed to repo)

### Checklist Items (Annex II)
- [ ] All stakeholders identified and informed
- [ ] Architecture diagram reviewed and approved
- [ ] Security invariants INV-01 through INV-07 acknowledged by TI representative
- [ ] Data matrix reviewed: which data flows cross tenant boundaries?
- [ ] LGPD compliance posture confirmed: no PII in logs or durable storage
- [ ] Write modes for each capability set to least-privilege defaults
- [ ] Rollback procedure for config publications understood by operator
- [ ] Monitoring / alerting owner named
- [ ] Pilot timeline agreed (start date, evaluation date, exit criteria)
- [ ] Non-scope items explicitly listed and countersigned

### How to Verify Fail-Closed Behavior
Run the unit test suite with `make test-security` and confirm all INV-0x tests pass.
Specifically verify `FailClosedError` is raised when `IARA_PRODUCTION_AUTHORIZED` is
absent and a production graph path is invoked.

### Common Rejection Reasons
- Missing signature from Digi2b TI representative
- Write modes not explicitly set (leaving defaults unconfirmed is not acceptable)
- HITL approver list missing or incomplete
- Non-scope section absent or does not reference cl. 14.2
- Pilot tenant IDs not confirmed in the database before G0 sign-off

---

## G1 — Infrastructure & Seed Verification

### Mandatory Artefacts
- [ ] `alembic upgrade head` completed without error on target environment
- [ ] All tables verified via `\dt` on target Postgres instance
- [ ] `make seed-pilot` completed; pilot tenant resolvable by `TenantResolver`
- [ ] RabbitMQ topology declared (exchanges, queues, DLX)
- [ ] Health check endpoint returns `{"status": "ok"}` at `GET /health`
- [ ] All `*_ref` secret references resolve from the configured secret store

### Evidence to Produce
- Screenshot or log of `alembic upgrade head` output
- SQL output of `SELECT tenant_key, status FROM tenants;` showing pilot tenant
- Screenshot of `GET /health` response
- RabbitMQ Management UI screenshot showing queues with zero messages

### Checklist Items (Annex II)
- [ ] `follow_up_queue` table exists with correct columns and partial index on `trigger_at`
- [ ] `hitl_holds` table exists with `run_id` unique constraint
- [ ] `config_publications` table exists with `is_active` column
- [ ] Pilot tenant has at least one `provider_accounts` record with correct `account_id_ref`
- [ ] `IARA_PILOT_WEBHOOK_KEY` stored as SHA-256 hash in `tenants.webhook_secret_hash`
- [ ] Worker starts and logs `job_consumer_ready`, `outbox_drainer_ready`, `follow_up_scheduler_ready`
- [ ] `GET /ready` returns 200 before any traffic is sent
- [ ] Secret store IAM/permissions tested from the runtime environment (not local dev)

### How to Verify Fail-Closed Behavior
Send a webhook with an unknown `tenant_key`. Confirm the API returns 404 and no
job is published to RabbitMQ (check queue depth stays zero).

### Common Rejection Reasons
- Migrations applied on local dev only, not on target environment
- Pilot tenant seeded with wrong `CHATWOOT_ACCOUNT_ID`
- `follow_up_queue` partial index missing (scheduler performance degrades)
- Secret refs pointing to development values in a staging/production environment

---

## G2 — Agent Functional Validation (Sandbox)

### Mandatory Artefacts
- [ ] End-to-end smoke test: webhook → RabbitMQ → LangGraph → outbox → Chatwoot MCP
- [ ] At least one message successfully sent to a real Chatwoot conversation in sandbox
- [ ] Guardrails node fires correctly on a blocked keyword (test with `blocklist.txt` word)
- [ ] Eligibility checker correctly rejects: duplicate event, debounced conversation, inactive tenant
- [ ] HITL node suspends run and `hitl_holds` record appears with `status='pending'`

### Evidence to Produce
- Structured log lines showing full message flow (event → outbox → confirmed)
- `SELECT * FROM provider_command_outbox WHERE status='confirmed' LIMIT 5;` result
- `SELECT * FROM hitl_holds WHERE status='pending';` result from HITL trigger test
- Blocklist trigger log showing `FailClosedError` or guardrails rejection

### Checklist Items (Annex II)
- [ ] `INV-01` verified: ambiguous tenant key raises `FailClosedError` (not 500)
- [ ] `INV-02` verified: cross-tenant account ID mismatch raises `CrossTenantError` in logs
- [ ] `INV-03` verified: LLM tool calls use logical names only (check agent logs, no `kanban_tasks_move` etc.)
- [ ] `INV-04` verified: no direct provider calls inside graph — all writes via outbox
- [ ] `INV-05` verified: no raw phone numbers, CPFs, or API tokens in log output
- [ ] `INV-06` verified: `close_conversation` / `update_contact` require HITL approval
- [ ] `INV-07` verified: production graph path blocked when `IARA_PRODUCTION_AUTHORIZED=false`
- [ ] Outbox retry logic verified: injecting a MCP 500 causes retry with exponential backoff
- [ ] Dead-letter queue populated after max retries exceeded

### How to Verify Fail-Closed Behavior
Set `IARA_ENV=production` and `IARA_PRODUCTION_AUTHORIZED=false`. Trigger a production
graph path. Confirm `FailClosedError` is raised and no side effects are executed.

### Common Rejection Reasons
- Any INV-0x security test failing
- HITL hold not persisted to Postgres (in-memory-only hold does not survive restart)
- Outbox commands executing before `IARA_PRODUCTION_AUTHORIZED=true` in production env
- LLM leaking raw MCP tool names into its reasoning (INV-03 violation)

---

## G3 — HITL & Config Publishing Validation

### Mandatory Artefacts
- [ ] HITL approve flow: `POST /hitl/{run_id}/approve` resumes graph and executes outbox command
- [ ] HITL reject flow: `POST /hitl/{run_id}/reject` terminates run without side effects
- [ ] Config draft→publish pipeline: draft created, published, active config retrieved
- [ ] Config rollback: roll back to a previous `publication_id` via `POST /config/{tenant_id}/rollback/{publication_id}`
- [ ] Stale HITL hold query returns correct results after 2+ hours (manual test)

### Evidence to Produce
- Log lines for `hitl_hold_registered_db`, `hitl_approved`, `hitl_rejected`
- `SELECT * FROM config_publications ORDER BY published_at DESC LIMIT 3;` after rollback
- `GET /config/{tenant_id}/active` response before and after rollback showing config change

### Checklist Items (Annex II)
- [ ] HITL approve restores graph state from LangGraph checkpointer (not from memory)
- [ ] HITL reject does not leave orphaned outbox commands in `pending` status
- [ ] Config publication sets exactly one `is_active=true` per tenant (all others set to false)
- [ ] Rolled-back publication immediately used by next LangGraph invocation
- [ ] HITL holds survive worker restart (Postgres-backed, not in-memory only)
- [ ] `GET /hitl/pending` returns all pending holds across tenants (operator view)
- [ ] `approved_by` / `rejected_by` fields populated with sanitized (non-PII) reference
- [ ] Config draft validation rejects invalid JSON schema before publish

### How to Verify Fail-Closed Behavior
Attempt to approve a hold with a mismatched tenant ID. Confirm `CrossTenantError` is
raised and the hold remains in `pending` status.

### Common Rejection Reasons
- HITL resume fails because checkpointer state was in-memory only (lost on restart)
- Config rollback activates multiple publications simultaneously (bug in `PublishService`)
- `approved_by` field stores a raw email address (PII in storage — INV-05 violation)

---

## G4 — Follow-Up Scheduler Validation

### Mandatory Artefacts
- [ ] Follow-up item enqueued via `schedule_followup` tool, visible in `follow_up_queue`
- [ ] `FollowUpSchedulerWorker` promotes due item to `provider_command_outbox`
- [ ] Outbox drainer delivers `followup_reengage_conversation` command to Chatwoot
- [ ] Opt-out flow: `mark_opted_out` prevents delivery; item marked `skipped` with `reason='opted_out'`
- [ ] Max-attempts exceeded: item marked `failed` after `max_attempts` retries

### Evidence to Produce
- `SELECT * FROM follow_up_queue WHERE status='sent' LIMIT 5;` after successful delivery
- `SELECT * FROM follow_up_queue WHERE status='skipped';` after opt-out test
- Log lines `follow_up_scheduler_ready`, `follow_up_scheduler_batch`, `follow_up_promoted_to_outbox`
- Outbox `capability_name='followup_reengage_conversation'` with `status='confirmed'`

### Checklist Items (Annex II)
- [ ] Follow-up `message_ref` stores SHA-256 hash only — no raw message text (INV-05)
- [ ] `contact_ref` stores SHA-256 hash only — no raw phone/CPF (INV-05)
- [ ] Idempotency key uniqueness: re-running scheduler on same item does not double-send
- [ ] Partial index `ix_follow_up_trigger` used for scheduler query (check `EXPLAIN` output)
- [ ] Scheduler handles DB connection failure gracefully (logs error, sleeps, retries)
- [ ] Follow-up count included in Prometheus metrics (`/metrics` endpoint)
- [ ] `attempt_count` incremented before outbox write (fail-safe: never loops infinitely)
- [ ] Worker shutdown (SIGTERM) completes current batch before stopping

### How to Verify Fail-Closed Behavior
Set `opted_out=true` on a pending item. Trigger the scheduler. Confirm the item is
marked `skipped` and no outbox command is created.

### Common Rejection Reasons
- Raw message text stored in `follow_up_queue` instead of hash (INV-05 violation)
- Scheduler creates duplicate outbox entries on retry (idempotency key not propagated correctly)
- `attempt_count` not incremented before outbox write (risk of infinite loop on error)

---

## G5 — Load & Resilience Testing

### Mandatory Artefacts
- [ ] 100 concurrent webhook requests processed without data loss
- [ ] Lease conflict rate under 5% at 50 concurrent conversations
- [ ] Outbox drain rate meets SLA (all commands confirmed within 60 seconds)
- [ ] Worker crash + restart: no duplicate messages, no lost outbox commands
- [ ] RabbitMQ DLX queue monitored and alerted

### Evidence to Produce
- Load test report (tool + parameters + pass/fail)
- `SELECT COUNT(*) FROM provider_command_outbox WHERE status='dead_lettered';` = 0 after load test
- Worker restart log showing clean shutdown and startup with zero message loss
- Prometheus metrics screenshot showing `iara_webhook_requests_total` and `iara_outbox_commands_total`

### Checklist Items (Annex II)
- [ ] No cross-tenant data leakage under concurrent load (verify by tenant isolation test)
- [ ] Lease TTL correctly expires after `IARA_LEASE_TTL_SECONDS` under load
- [ ] RabbitMQ prefetch count set correctly (`RABBITMQ_PREFETCH_COUNT`) — not set to 0
- [ ] `FollowUpSchedulerWorker` does not starve under high outbox write pressure
- [ ] HITL holds under load: concurrent approve/reject calls correctly serialized
- [ ] `idempotency_records` table shows no duplicate events processed under replay test
- [ ] Graceful shutdown (SIGTERM) drains in-flight messages before exit

### How to Verify Fail-Closed Behavior
Kill the worker mid-flight on a high-risk command. Confirm the outbox command remains
in `pending` or `sent` status and is correctly retried on restart.

### Common Rejection Reasons
- Duplicate messages delivered under replay (idempotency not enforced end-to-end)
- Lease not released after worker crash (stuck lease blocks further processing)
- DLX queue growing unbounded without alerting

---

## G6 — Production Authorization

### Mandatory Artefacts
- [ ] `IARA_ENV=production` and `IARA_PRODUCTION_AUTHORIZED=true` set in production environment
- [ ] All `*_ref` secrets resolve from production secret store (not dev values)
- [ ] Final sign-off from all parties listed in G0 decision document
- [ ] Post-deployment smoke test on production passing
- [ ] Monitoring dashboard live and showing healthy metrics
- [ ] On-call runbook reviewed by operator

### Evidence to Produce
- `GET /health` returning `{"status": "ok", "env": "production"}` from production
- Secret store audit showing all secrets populated (not default/empty)
- Signed G6 sign-off email or document from stakeholders
- Prometheus dashboard screenshot with active metrics

### Checklist Items (Annex II)
- [ ] `IARA_PRODUCTION_AUTHORIZED=true` explicitly set — not inherited from env
- [ ] Write modes reviewed: kanban and campaign modes match agreed G0 settings
- [ ] HITL approver list matches G0 decision document (operators with access to `/hitl/*`)
- [ ] `LOG_FORMAT=json` in production (structured logs for log aggregation)
- [ ] RabbitMQ DLX alert configured (alert fires if `dead_lettered` queue > 0 for > 5 min)
- [ ] Follow-up queue overdue alert configured (fires if pending items with `trigger_at < NOW() - 10min`)
- [ ] HITL stale hold alert configured (fires if any hold pending > 2 hours)
- [ ] Rollback procedure tested: `POST /config/{tenant_id}/rollback/{publication_id}` verified working
- [ ] Data retention policy confirmed: `event_receipts` and `conversation_debounce` TTL configured
- [ ] Incident response contacts up to date in runbook

### How to Verify Fail-Closed Behavior
Temporarily unset `IARA_PRODUCTION_AUTHORIZED` and confirm the next request to a
production-only code path returns a clear error, not a silent fallback.

### Common Rejection Reasons
- `IARA_PRODUCTION_AUTHORIZED` set globally in CI/CD pipeline (risk of accidental production writes)
- Monitoring not live at time of sign-off
- Stale HITL holds from G5 testing not cleared before going to production
- On-call runbook not reviewed by the actual on-call operator
