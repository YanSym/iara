# IAra — G0 Kickoff Decision Document

**Document ID:** G0-[YYYYMMDD]-[TENANT_NAME]
**Date:** [FILL IN]
**Version:** 1.0
**Status:** DRAFT | APPROVED

---

## 1. Purpose

This document records the architectural and operational decisions made at the G0
kickoff gate for the IAra deployment. All items must be resolved before the team
proceeds to G1 (Infrastructure & Seed Verification). A signed copy of this
document is required as a mandatory artefact at every subsequent gate review.

---

## 2. Parties

| Role | Name | Organization | Contact |
|------|------|-------------|---------|
| Project Owner | [FILL IN] | [FILL IN] | [FILL IN] |
| Digi2b TI Representative | Breno Cocheto | Digi2B | breno@digi2b.com |
| Operator / On-Call | [FILL IN] | [FILL IN] | [FILL IN] |
| Security Reviewer | [FILL IN] | [FILL IN] | [FILL IN] |

---

## 3. Scheduling Provider Choice

**Decision:** [ ] Google Calendar (GCal) [ ] Clinicorp [ ] NullSchedulingWriteAdapter (disabled)

**Rationale:**
> [FILL IN — explain why this provider was chosen, or why scheduling is disabled]

**Configuration:**
```
GOOGLE_CALENDAR_ENABLED=false | true
CLINICORP_ENABLED=false | true
```

If scheduling is disabled (`NullSchedulingWriteAdapter`), confirm that follow-up
scheduling tool calls will be silently dropped (no error, no side effect).

---

## 4. Kanban Initial Mode

**Decision:** [ ] `suggest_only` [ ] `write_sandbox` [ ] `write_confirmed`

| Mode | Behavior |
|------|----------|
| `suggest_only` | Agent proposes kanban moves; no writes executed |
| `write_sandbox` | Writes executed in Chatwoot sandbox account only |
| `write_confirmed` | Writes executed in live Chatwoot account |

**Selected mode:** [FILL IN]

**Rationale:**
> [FILL IN — justify the selected mode for go-live. `suggest_only` is the minimum-risk default.]

**Configuration:**
```
IARA_KANBAN_DEFAULT_MODE=suggest_only
```

---

## 5. Follow-Up Mode

**Decision:** [ ] `disabled` [ ] `enabled_sandbox` [ ] `enabled_production`

| Mode | Behavior |
|------|----------|
| `disabled` | `FollowUpSchedulerWorker` starts but skips all items |
| `enabled_sandbox` | Follow-ups delivered to sandbox Chatwoot only |
| `enabled_production` | Follow-ups delivered to live contacts |

**Selected mode:** [FILL IN]

**Maximum attempts per follow-up item:** [FILL IN — default: 3]

**Quiet hours policy:** [FILL IN — e.g., no follow-ups between 20:00–08:00 BRT]

**Rationale:**
> [FILL IN — justify. `disabled` is the minimum-risk default for G0.]

---

## 6. Pilot Tenants

List all tenants that will be active during the pilot phase.

| Tenant Name | Tenant Key (webhook path) | Chatwoot Account ID | HITL Approver(s) |
|-------------|--------------------------|--------------------|--------------------|
| [FILL IN] | [FILL IN] | [FILL IN] | [FILL IN] |

**Pilot Tenant IDs and webhook keys must be stored in the agreed secret vault before G1.**
Do not commit these values to this document or to version control.

Vault location for pilot secrets: [FILL IN — e.g., AWS Secrets Manager path]

---

## 7. HITL Approvers

Human-in-the-Loop approval is required for high-risk actions (INV-06):
- `close_conversation`
- `update_contact`
- Any capability with `risk_class=HIGH_WRITE`

**Named HITL approvers for this deployment:**

| Name | Role | Contact | Availability |
|------|------|---------|-------------|
| [FILL IN] | [FILL IN] | [FILL IN] | [FILL IN — e.g., business hours BRT] |

**Escalation path if approver is unavailable:**
> [FILL IN — e.g., stale holds > 2 hours escalate to project owner]

**HITL stale hold SLA:** [FILL IN — e.g., resolve within 2 business hours]

---

## 8. Data Matrix

Describes what data flows through IAra and across which boundaries.

| Data Type | Source | Destination | PII? | How Handled |
|-----------|--------|-------------|------|-------------|
| Incoming message content | Chatwoot webhook | LangGraph context | Yes | Truncated at `IARA_MAX_CONTEXT_MESSAGES`; not persisted |
| Contact identifier | Chatwoot webhook | `follow_up_queue.contact_ref` | Yes | SHA-256 hash only (INV-05) |
| Follow-up message text | Agent output | `follow_up_queue.message_ref` | Potentially | SHA-256 hash + length only (INV-05) |
| Outbox command parameters | LangGraph | `provider_command_outbox.parameters_json` | Potentially | Sanitized by `RedactionProcessor`; no raw tokens |
| Audit events | Application | `safe_audit_events` | No | Opaque hashes and counts only (INV-05) |
| LangGraph state | LangGraph checkpointer | Postgres | Potentially | Only sanitized snapshots in `hitl_holds.context_snapshot` |

**LGPD data residency requirement:** [FILL IN — e.g., all data must remain in Brazil (AWS sa-east-1)]

**Data retention policy:**
- `event_receipts`: [FILL IN — e.g., 90 days TTL]
- `conversation_debounce`: [FILL IN — e.g., 24 hours TTL]
- `follow_up_queue` (sent/skipped/failed): [FILL IN — e.g., 30 days]
- `hitl_holds` (resolved): [FILL IN — e.g., 90 days]

---

## 9. Sandbox Environment

**Sandbox confirmed available:** [ ] Yes [ ] No

| Resource | URL / Endpoint | Notes |
|----------|---------------|-------|
| Chatwoot sandbox instance | [FILL IN] | Separate account from production |
| Postgres (sandbox) | [FILL IN] | |
| RabbitMQ (sandbox) | [FILL IN] | |

**Sandbox Chatwoot account ID:** [FILL IN]
**Sandbox MCP slug:** [FILL IN]

Confirm that sandbox Chatwoot is isolated from live customer data:
- [ ] Sandbox uses a dedicated Chatwoot account (different account ID from production)
- [ ] Sandbox contacts do not receive real notifications
- [ ] Sandbox has at least one test conversation pre-created for smoke testing

---

## 10. Write Modes Summary

| Capability | Mode | Env Var | Value |
|------------|------|---------|-------|
| Kanban updates | [FILL IN] | `IARA_KANBAN_DEFAULT_MODE` | [FILL IN] |
| Campaign sends | [FILL IN] | `IARA_CAMPAIGN_DEFAULT_MODE` | [FILL IN] |
| Follow-up scheduler | [FILL IN] | (see §5) | [FILL IN] |
| Scheduling provider | [FILL IN] | (see §3) | [FILL IN] |
| Production guard | Must be `false` at G0 | `IARA_PRODUCTION_AUTHORIZED` | `false` |

**Note:** `IARA_PRODUCTION_AUTHORIZED=true` must not be set until G6 sign-off.

---

## 11. Security Invariants Acknowledgement

The following security invariants are non-negotiable and cannot be overridden by
configuration. All parties acknowledge their existence and implications.

| Invariant | Description | Acknowledged |
|-----------|-------------|-------------|
| INV-01 | Fail-closed: any ambiguity raises `FailClosedError` | [ ] |
| INV-02 | No cross-tenant: provider account re-verified before each write | [ ] |
| INV-03 | LLM isolation: LLM sees only logical tool names, never raw MCP names | [ ] |
| INV-04 | Outbox-only side effects: no direct provider calls inside graph | [ ] |
| INV-05 | No PII in storage: SHA-256 hash refs only in durable storage | [ ] |
| INV-06 | High-risk writes require HITL approval | [ ] |
| INV-07 | Production graph blocked until `IARA_PRODUCTION_AUTHORIZED=true` | [ ] |

---

## 12. Pilot Timeline

| Milestone | Target Date | Owner |
|-----------|------------|-------|
| G1 complete (infra + seed) | [FILL IN] | [FILL IN] |
| G2 complete (agent functional) | [FILL IN] | [FILL IN] |
| G3 complete (HITL + config publishing) | [FILL IN] | [FILL IN] |
| G4 complete (follow-up scheduler) | [FILL IN] | [FILL IN] |
| G5 complete (load + resilience) | [FILL IN] | [FILL IN] |
| G6 complete (production authorization) | [FILL IN] | [FILL IN] |

**Pilot evaluation date:** [FILL IN — date to assess whether to expand beyond pilot tenants]

**Exit criteria for pilot expansion:**
> [FILL IN — e.g., zero P1 incidents, HITL approve latency < 2h, 95% of follow-ups delivered]

---

## 13. Monitoring & Alerting

| Alert | Threshold | Owner | Channel |
|-------|-----------|-------|---------|
| DLX (dead-lettered) messages > 0 for > 5 min | Any | [FILL IN] | [FILL IN] |
| Follow-up overdue (pending, trigger_at < NOW() - 10min) | Any | [FILL IN] | [FILL IN] |
| HITL hold stale (pending > 2h) | Any | [FILL IN] | [FILL IN] |
| Worker process down | Process missing | [FILL IN] | [FILL IN] |
| API error rate > 1% | 5-min window | [FILL IN] | [FILL IN] |

---

## 14. Non-Scope Confirmation (cl. 14.2)

The following items are explicitly **outside the scope** of this deployment.
Any requests to implement these items must go through a new G0 gate.

- [ ] Multi-agent orchestration (more than one IAra instance per tenant)
- [ ] Direct database writes from the LangGraph agent (all writes must go via outbox)
- [ ] Storage of raw message text, phone numbers, or CPFs in any durable table
- [ ] Sending campaigns without HITL approval (campaign mode must remain `draft_only` or `dry_run` at G0)
- [ ] Integration with providers not listed in §3 (scheduling provider)
- [ ] Modification of security invariants INV-01 through INV-07

**Additional items confirmed out of scope for this deployment:**

> [FILL IN — list any project-specific exclusions]

All parties confirm that the above items are out of scope and that no work will be
performed on them under this G0 authorization.

---

## 15. Signatures

By signing below, each party confirms that they have read and understood this
document, agree with the decisions recorded herein, and authorize the project to
proceed to G1.

| Name | Role | Signature | Date |
|------|------|-----------|------|
| [FILL IN] | Project Owner | | |
| Breno Cocheto | Digi2b TI Representative | | |
| [FILL IN] | Operator / On-Call | | |
| [FILL IN] | Security Reviewer | | |

---

*End of G0 Kickoff Decision Document*
