# Open Decisions

> Track all unresolved scope or configuration decisions here.
> Update this file as decisions are made. Do NOT silently pick defaults for
> scope-defining decisions (1, 5, 6, 8, 13); emit `DECISION NEEDED:` and
> proceed only on the parts that are unambiguous.

---

## Decision 1 — First Delivery Scope

**DECISION NEEDED:** Which features are in the mandatory first delivery vs. technical backlog?

**Default while open:** Implement Phases 0–4 (foundations, contracts, provider, persistence,
graph, media, agent tools). Phases 5–8 (command/campaign, memory/KB, observability, MCP
catalog) are implemented as stubs/skeletons ready for activation.

**Status:** Open

---

## Decision 2 — Pilot Tenant / Account / Inbox

**DECISION NEEDED:** Which tenant(s)/accounts/inboxes are used in the controlled pilot?

**Default while open:** All tests run against synthetic fixtures; no real account IDs are used.

**Status:** Open

---

## Decision 3 — Priority Scheduling Backend

**DECISION NEEDED:** Priority scheduling backend: Google Calendar, Clinicorp, or custom adapter?

**Default while open:** Fake/stub backend used for all scheduling tests. Both Google Calendar
and Clinicorp adapters are implemented as stubs pending real credentials.

**Status:** Open

---

## Decision 4 — Write Sandbox vs. Blocked

**DECISION NEEDED:** Which writes go to sandbox and which stay blocked?

**Default while open:** All provider writes go to the fake stub adapter; no real writes are
executed. Outbox accumulates but adapter is a no-op stub.

**Status:** Open

---

## Decision 5 — Initial Kanban Mode

**DECISION NEEDED:** Initial kanban mode: `suggest_only`, `write_sandbox`, or `write_confirmed`?

**Default while open:** `suggest_only` — kanban analysis is performed but no writes to
Chatwoot are attempted. Policy is required to enable writes.

**Status:** Open (code default: `suggest_only`)

---

## Decision 6 — Initial Campaign Mode

**DECISION NEEDED:** Initial campaign mode: `draft_only`, `dry_run`, `sandbox`, or `approved_send`?

**Default while open:** `draft_only` — campaigns produce drafts only. No messages are sent.

**Status:** Open (code default: `draft_only`)

---

## Decision 7 — High-Risk Action Approvers

**DECISION NEEDED:** Who approves high-risk actions per tenant/environment?

**Default while open:** HITL interrupt points are in place but approval is simulated by
test fixtures. Real approvers must be configured in `command_requester_bindings`.

**Status:** Open

---

## Decision 8 — Tenant MCP Catalog Governance

**DECISION NEEDED:** Who maintains `tenant_mcp_servers` and approves custom MCP?

**Default while open:** Phase 8 (MCP catalog) is not activated. Only managed/default MCPs
(Chatwoot stub, Google Calendar stub, Clinicorp stub) are registered.

**Status:** Open

---

## Decision 9 — Retention / TTL / Purge / Anonymize Policy

**DECISION NEEDED:** Retention / TTL / purge / anonymize policy.

**Default while open:** Memory is disabled (`IARA_MEMORY_ENABLED=false`). Event receipts
and audit events carry a `ttl_days=90` default. No anonymize jobs are scheduled.

**Status:** Open

---

## Decision 10 — LGPD / Security Review / DPA / NDA Owner

**DECISION NEEDED:** Owner of LGPD / security review / DPA / NDA.

**Default while open:** No personal data is processed in tests (synthetic fixtures only).
All redaction guards are in place. Awaiting legal assignment.

**Status:** Open

---

## Decision 11 — Sandbox Environment and Synthetic Data

**DECISION NEEDED:** Sandbox environment and synthetic data availability.

**Default while open:** Local docker-compose provides the sandbox. Synthetic fixtures live
in `tests/fixtures/`. No real tenant data is used.

**Status:** Open

---

## Decision 12 — SLA for Critical Security Failures

**DECISION NEEDED:** SLA for fixing critical security failures.

**Default while open:** Not defined. Any security test failure blocks CI.

**Status:** Open

---

## Decision 13 — Dashboard / Config Organizer Scope

**DECISION NEEDED:** Whether dashboard / config organizer is in the first delivery or a later phase.

**Default while open:** Phase 8 admin surface is not activated. Config publishing backend
(draft→validate→publish) is implemented but no frontend surface exists.

**Status:** Open

---

## Decision 14 — DER and Visual Architecture Deliverables

**DECISION NEEDED:** Whether the detailed DER and detailed visual page ship now or stay as visual backlog.

**Default while open:** Logical DER is documented in `docs/der.md`. No visual diagrams are
generated automatically.

**Status:** Open
