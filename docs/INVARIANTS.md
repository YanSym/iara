# IAra Runtime — Non-Negotiable Invariants

> These invariants are hard constraints. Any code, test, or design that violates one of these
> is a **defect**, regardless of how convenient the violation is. The security test suite
> imports this document as the source of truth for adversarial checks.

---

## INV-01 — Fail-Closed

Any ambiguity about `tenant`, `provider_account`, `inbox`, `source_channel`, or `capability`
**MUST** block the operation.

- No permissive fallback.
- No similarity inference.
- No "best guess" execution.
- Violation: `FailClosedError` raised before any external call.

## INV-02 — No Cross-Tenant

Tenant context is threaded through every call and **re-verified immediately before any
external side effect**. A binding mismatch raises `CrossTenantError` and aborts before any
network call.

## INV-03 — LLM Never Touches Raw Provider MCP

The model **never** receives the raw Chatwoot MCP catalog and **never** chooses a real MCP
tool name. Capabilities are resolved through an explicit registry + policy. The model only
sees logical, published Agent Tools with sanitized names.

## INV-04 — Effectively-Once Side Effects

Every external side effect goes through the **outbox → idempotency → readback** pattern.
A side effect is **NEVER** executed directly inside a LangGraph node that can be replayed
from a checkpoint.

## INV-05 — No Secrets, PII, or Raw Payloads in Durable Storage

Logs, audit events, evidence, and committed files contain **only**:
- hashes (SHA-256 refs)
- counts
- statuses
- sanitized error messages (no stack traces with PII)

Secrets are referenced by `secret_ref` / `credential_ref` **only**. Never stored inline.

Prohibited in any durable record:
- tokens, headers, cookies, credentials
- account IDs (real)
- raw phone numbers
- raw webhook payloads
- pinData, temporary URLs
- raw attachments, audio, images, base64
- full MCP dumps
- full lead/contact lists
- full conversation contents
- sensitive internal prompts or chain-of-thought

## INV-06 — High-Risk Writes Are Gated

- Campaigns default to `draft_only` / `dry_run`.
- Kanban defaults to `suggest_only`.
- Real sends require explicit policy + HITL where applicable.
- Violation: `PolicyViolationError` raised.

## INV-07 — Production Is Blocked

No code path may target real production tenants/accounts without an explicit, configured
`IARA_PRODUCTION_AUTHORIZED=true` flag. Default config points only at sandbox/synthetic data.

---

## Security Test References

| Invariant | Test File |
|-----------|-----------|
| INV-01 | `tests/security/test_fail_closed.py` |
| INV-02 | `tests/security/test_cross_tenant.py` |
| INV-03 | `tests/security/test_mcp_isolation.py` |
| INV-04 | `tests/integration/test_idempotency.py` |
| INV-05 | `tests/security/test_redaction.py` |
| INV-06 | `tests/security/test_policy_guards.py` |
| INV-07 | `tests/security/test_production_guard.py` |
