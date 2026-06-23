# IAra Multi-Tenant — Implementation Continuation Plan

> **Purpose**: Sequential implementation roadmap for all gaps between the current codebase
> and the contractual requirements defined in _Contrato IAra Multi-Tenant v1.1_ (Digi2b ↔ SymCorp).
> Cards are ordered so each item can be implemented without depending on a later card.
> A downstream agent should read this file in full before touching any file.
>
> **Stack**: Python 3.13, FastAPI, LangGraph, SQLAlchemy 2.0 async, RabbitMQ, Postgres, PEP 8,
> English docstrings, type hints everywhere, `ruff` + `black` compliant.
>
> **Guiding principles**
> - No mock/stub left in any production path — every `return {}` or hardcoded value is a defect.
> - Every DB-backed entity must survive a process restart.
> - Every outbox command must have a registered capability that can resolve it without raising
>   `FailClosedError`.
> - Tests must remain green after each card. Run `python -m pytest tests/unit tests/security -x -q`
>   after every card before committing.

---

## EPIC 1 — Critical Production Bugs (P0 — fix before any other work)

These bugs cause hard crashes or silent data loss in production. Nothing else matters until these are fixed.

---

### IARA-001 · Bug · P0 · Effort: S (2 h)

**Title**: Fix `workers/main.py` — `ChatwootMcpAdapter` instantiated without required `account_id` and `mcp_slug`

**Why**:
`ChatwootMcpAdapter.__init__` requires `account_id: str` and `mcp_slug: str` as mandatory
parameters (no defaults). `workers/main.py` passes only `registry`, `mcp_base_url`,
`credential_ref`, `timeout_seconds`, `max_retries`. This is a `TypeError` at worker startup —
the outbox drainer never starts in production.
`settings.chatwoot_account_id` and `settings.chatwoot_mcp_slug` already exist in `config/settings.py`
but are not forwarded.

**What to implement**:

In `src/iara/workers/main.py`, locate the `ChatwootMcpAdapter(...)` instantiation and add the two
missing keyword arguments:

```python
adapter = ChatwootMcpAdapter(
    registry=mcp_registry,
    mcp_base_url=settings.chatwoot_mcp_base_url,
    account_id=settings.chatwoot_account_id,   # ADD THIS
    mcp_slug=settings.chatwoot_mcp_slug,        # ADD THIS
    credential_ref=settings.chatwoot_mcp_credential_ref,
    timeout_seconds=settings.chatwoot_mcp_timeout_seconds,
    max_retries=settings.chatwoot_mcp_max_retries,
)
```

Also update `tests/unit/test_gaps_6_to_10.py` `_make_adapter()` helper if it does not already
pass these args (check first — it was already updated in a previous session).

**Acceptance criteria**:
- `python -m pytest tests/unit/test_gaps_6_to_10.py -x -q` passes.
- `python -c "from iara.workers.main import build_outbox_worker; print('ok')"` does not raise
  `TypeError`.

**Dependencies**: None.

---

### IARA-002 · Bug · P0 · Effort: M (4 h)

**Title**: Wire `IdempotencyRepository` and `DebounceRepository` into `EligibilityChecker` in webhook handler

**Why**:
`EligibilityChecker` implements 8 rules. Rules 7 (idempotency) and 8 (debounce) check whether
an event is a duplicate or within a debounce window. Both repositories are fully implemented in
`src/iara/persistence/` but `src/iara/api/routers/webhooks.py` instantiates `EligibilityChecker`
without passing them — so duplicate messages and rapid re-fires are silently passed through.
Gate G4 criterion: *"duplicate, burst, concurrency without duplication"*.

**What to implement**:

1. In `src/iara/eligibility/decision.py`, confirm the `EligibilityChecker.__init__` signature
   accepts `idempotency_checker` and `debounce_checker` as optional protocol-typed arguments.
   If they are currently stubs (`return False`), replace them with real delegation:
   ```python
   def _check_idempotency(self, event: NormalizedChatwootEvent) -> bool:
       """Return True (duplicate) if this event hash was already processed."""
       if self._idempotency_checker is None:
           return False
       return self._idempotency_checker.is_duplicate(event.idempotency_key)

   def _check_debounce(self, event: NormalizedChatwootEvent) -> bool:
       """Return True (debounced) if conversation is within the debounce window."""
       if self._debounce_checker is None:
           return False
       return self._debounce_checker.is_debounced(event.conversation_id)
   ```

2. In `src/iara/api/routers/webhooks.py`, inject both repositories into `EligibilityChecker`:
   ```python
   idempotency_repo = IdempotencyRepository(db_session)
   debounce_repo = DebounceRepository(db_session)
   checker = EligibilityChecker(
       idempotency_checker=idempotency_repo,
       debounce_checker=debounce_repo,
   )
   ```
   Both repos already accept an async session. Use the `get_db_session` FastAPI dependency.

3. After accepting an event, call `idempotency_repo.mark_seen(event.idempotency_key)` and
   `debounce_repo.set_window(event.conversation_id, window_seconds=settings.debounce_window_seconds)`.
   Add `debounce_window_seconds: int = Field(default=3, ge=1, le=60)` to `settings.py` if missing.

**Acceptance criteria**:
- Sending the same synthetic event twice within 1 second → second call returns `200` with
  `"status": "debounced"`.
- The `test_eligibility.py` tests remain green with the wired repos.

**Dependencies**: None.

---

### IARA-003 · Bug · P0 · Effort: M (5 h)

**Title**: Implement `PostgresTenantRepository` and replace `InMemoryTenantRepository` in production webhook path

**Why**:
`src/iara/api/routers/webhooks.py` builds `TenantResolver` with `InMemoryTenantRepository`,
a test stub that reads from hardcoded dicts. In production this means tenant validation never
reads from Postgres — any tenant_key not in the in-memory dict silently fails closed, and any
tenant configured via the DB is invisible. Gate G0: *"tenant/account/inbox bindings documented"*.

**What to implement**:

1. Create `src/iara/tenancy/postgres_repository.py`:
   ```python
   class PostgresTenantRepository:
       """Postgres-backed tenant lookup implementing the TenantRepository protocol."""

       def __init__(self, session: AsyncSession) -> None: ...

       async def get_by_key(self, tenant_key: str) -> TenantContext | None:
           """Fetch tenant by webhook key hash; return None if not found or inactive."""
           row = await session.execute(
               select(Tenant).where(Tenant.webhook_key_hash == _hash(tenant_key))
           )
           tenant = row.scalar_one_or_none()
           if tenant is None or not tenant.is_active:
               return None
           return TenantContext(
               tenant_id=str(tenant.id),
               tenant_key=tenant_key,
               is_active=tenant.is_active,
               provider_account_id=tenant.default_provider_account_id,
           )
   ```

2. In `src/iara/api/routers/webhooks.py`, replace `InMemoryTenantRepository` with
   `PostgresTenantRepository(db_session)` when `settings.iara_env` is not `development`.
   Keep `InMemoryTenantRepository` available as a local dev/test fallback, gated by:
   ```python
   if settings.iara_env in (Environment.DEVELOPMENT, Environment.SANDBOX):
       repo = InMemoryTenantRepository(_DEV_TENANTS)
   else:
       repo = PostgresTenantRepository(db_session)
   ```

3. Add `webhook_key_hash` column to the `tenants` ORM model if absent (check migration 0001).
   If missing, create migration `0004_add_tenant_webhook_key_hash.py`.

**Acceptance criteria**:
- With `IARA_ENV=staging`, a webhook request with a valid tenant_key stored in Postgres
  resolves correctly.
- `InMemoryTenantRepository` is still used in development/sandbox environments.
- `tests/unit/test_eligibility.py` and `tests/security/test_cross_tenant.py` remain green.

**Dependencies**: None.

---

### IARA-004 · Bug · P0 · Effort: M (4 h)

**Title**: Wire `LeaseRepository` into `JobConsumerWorker` for exclusive per-conversation processing

**Why**:
`LeaseRepository` implements PostgreSQL advisory-lock-style fencing tokens (full implementation
confirmed). `JobConsumerWorker` in `workers/job_consumer.py` does not use it — two RabbitMQ
messages for the same conversation can be processed concurrently, producing duplicate side effects
and conflicting agent state. Gate G4: *"concurrency controlled; side effects do not duplicate"*.

**What to implement**:

In `src/iara/workers/job_consumer.py`, wrap the graph invocation with a lease acquisition:

```python
async def _process_job(self, job: ConversationJob) -> None:
    """Acquire a per-conversation lease before invoking the graph.

    Prevents concurrent processing of the same conversation. If the lease
    is held by another worker, the message is nacked for redelivery.
    """
    async with self._db_session_factory() as session:
        lease_repo = LeaseRepository(session)
        lease_token = await lease_repo.try_acquire(
            conversation_id=job.conversation_id,
            tenant_id=job.tenant_id,
            ttl_seconds=settings.conversation_lease_ttl_seconds,
        )
        if lease_token is None:
            logger.warning("lease_conflict", conversation_id=job.conversation_id)
            raise LeaseConflictError(job.conversation_id)
        try:
            await self._invoke_graph(job)
        finally:
            await lease_repo.release(job.conversation_id, lease_token)
```

`LeaseConflictError` should trigger a nack+requeue with a short delay (use `basic_nack` with
`requeue=True` and a `asyncio.sleep(2)` before requeue to avoid hot loops).

Add `conversation_lease_ttl_seconds: int = Field(default=120, ge=10, le=600)` to `settings.py`
if absent.

**Acceptance criteria**:
- Two simultaneous messages for the same conversation: second is requeued, not processed
  concurrently.
- `tests/integration/test_idempotency.py` remains green.

**Dependencies**: None.

---

### IARA-005 · Bug · P0 · Effort: L (6 h)

**Title**: Fix `ToolExecutor._dispatch_read_tool()` — replace all inline hardcoded stubs with real catalog module delegation

**Why**:
`ToolExecutor._dispatch_read_tool()` contains hardcoded stub responses for every read tool
(e.g., `available_slots: 3`, `next_available: "2026-06-10T09:00:00"`,
`suggested_stage: "nurturing"`). The real catalog module handlers (`catalog/scheduling.handle_availability`,
`catalog/kanban.handle_kanban_analyze`, etc.) are never called in the execution path.
The scheduling adapter injected by `build_production_graph()` is wired to the executor but
never invoked. Gate G5: *"agenda consults before scheduling"*.

**What to implement**:

In `src/iara/tools/executor.py`, replace each stub in `_dispatch_read_tool()` with delegation
to the corresponding catalog module:

```python
async def _dispatch_read_tool(
    self,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Route read-only tool invocations to catalog handlers.

    Each handler is responsible for sanitising its own output (no PII,
    no raw provider data). Errors are caught and returned as structured
    error dicts so the agent can handle them gracefully.
    """
    match tool_name:
        case "check_availability":
            return await scheduling.handle_availability(
                arguments, adapter=self._scheduling_adapter
            )
        case "kanban_analyze":
            return await kanban.handle_kanban_analyze(
                arguments, tenant_id=self._tenant_id
            )
        case "campaign_status":
            return await campaigns.handle_campaign_status(arguments)
        case "campaign_validate_audience":
            return await campaigns.handle_campaign_validate_audience(arguments)
        case "lead_search":
            return await lead.handle_lead_search(arguments)
        case "history_analyze":
            return await history.handle_history_analyze(arguments)
        case "kb_suggest":
            return await kb.handle_kb_suggest(arguments)
        case _:
            raise FailClosedError(f"No read handler registered for tool: {tool_name!r}")
```

Update each catalog handler signature to accept an optional `adapter` or `tenant_id` kwarg
as needed (e.g., `handle_availability` needs the scheduling adapter to call
`provider.get_availability()`). Keep backward compatibility — default to `None` so existing
unit tests do not break; when `adapter is None`, fall back to a minimal stub with a clear log
warning.

Update `ToolExecutor.__init__` to store `tenant_id` so `_dispatch_read_tool` can pass it.

**Acceptance criteria**:
- `check_availability` with a real `GoogleCalendarProvider` injected returns live free-slot
  count (integration test with mocked HTTP).
- `kanban_analyze` returns a stage based on conversation context, not always `"nurturing"`.
- All `tests/unit/test_catalog_tools.py` and `tests/unit/test_executor.py` remain green.

**Dependencies**: None (catalog module handlers already exist; only the dispatch wiring changes).

---

## EPIC 2 — Persistence Layer Completion (P1)

These cards make persistent state survive process restarts. Required for gate G4 and all
subsequent gates.

---

### IARA-006 · Feature · P1 · Effort: L (8 h)

**Title**: Persist config drafts and publications to Postgres via `AgentConfigVersion` and `ConfigPublication` ORM models

**Why**:
`PublishService` in `src/iara/config_publishing/publisher.py` stores all drafts and publications
in Python dicts (`self._drafts`, `self._publications`). On process restart, all published tenant
configurations are lost and the runtime falls back to code defaults. Gate G0:
*"runtime reads only active published config"*. Gate G6: *"rollback documented"*.
The ORM models `AgentConfigVersion` and `ConfigPublication` already exist but are never written to.

**What to implement**:

1. Verify migration `0001` or `0002` includes `agent_config_versions` and `config_publications`
   tables. If absent, create migration `0004_add_config_tables.py` (or the next available
   sequence number) with the DDL matching the ORM models.

2. Rewrite `PublishService` to be fully async and DB-backed:

   ```python
   class PublishService:
       """Tenant configuration publishing pipeline backed by Postgres.

       Implements the draft → validate → publish → rollback lifecycle.
       The runtime always reads the row with status='active' and the
       highest published_at timestamp for a given tenant_id.
       """

       def __init__(self, tenant_id: str, session_factory: AsyncSessionFactory) -> None: ...

       async def create_draft(self, config_data: dict[str, Any]) -> str:
           """Persist a new draft; return its UUID draft_id."""

       async def publish(self, draft_id: str) -> str:
           """Validate draft, set previous active publication to 'superseded',
           insert new ConfigPublication with status='active'. Return publication_id."""

       async def get_active_publication(self) -> dict[str, Any] | None:
           """Return config_data of the current active publication, or None."""

       async def rollback(self, publication_id: str) -> None:
           """Restore a previous publication as active (immutable — does not delete)."""
   ```

3. Update `src/iara/config_publishing/registry.py` to work with the async `PublishService`.
   `get_kanban_stages(tenant_id)` becomes `async get_kanban_stages(tenant_id)`.

4. Update `src/iara/api/routers/config.py` endpoints to use `await` on all service calls.

5. Update the kanban catalog `build_kanban_update_command` to `await get_kanban_stages(...)`.

**Acceptance criteria**:
- `POST /config/{tenant_id}/draft` → `POST /config/{tenant_id}/draft/{id}/publish` →
  process restart → `GET /config/{tenant_id}/active` returns the published config.
- Rollback restores a previous config and the new active is visible without restart.
- All config router tests pass.

**Dependencies**: IARA-001 through IARA-004 (to have a stable base).

---

### IARA-007 · Feature · P1 · Effort: M (5 h)

**Title**: Implement DB-backed `HitlHoldRepository` and wire it into the graph HITL node and HITL router

**Why**:
`HitlHoldRegistry` is an in-memory dict. The `hitl_holds` Postgres table created in migration
`0003` is never written to. On restart, all pending HITL holds are lost — operators cannot
approve/reject paused conversations after a worker restart. Gate G5:
*"writes with outbox/idempotency/readback"* and HITL policy.

**What to implement**:

1. Create `src/iara/persistence/repositories/hitl_repository.py`:

   ```python
   class HitlHoldRepository:
       """Postgres-backed HITL hold lifecycle manager."""

       async def register_hold(
           self,
           run_id: str,
           tenant_id: str,
           conversation_id: str,
           reason: str,
           context_snapshot: dict[str, Any],
       ) -> None:
           """Insert a new hold with status='pending'."""

       async def list_pending(self, tenant_id: str) -> list[HitlHoldRecord]: ...

       async def approve(self, run_id: str, approved_by: str) -> None:
           """Set status='approved', record approver and timestamp."""

       async def reject(self, run_id: str, rejected_by: str, reason: str) -> None:
           """Set status='rejected'."""

       async def get(self, run_id: str) -> HitlHoldRecord | None: ...
   ```

2. In the graph HITL interruption path (wherever `hitl_requested=True` is set in state),
   inject the repository and call `await hitl_repo.register_hold(...)` before routing to END.
   The `run_id` is available from the LangGraph `RunnableConfig`.

3. In `src/iara/api/routers/hitl.py`, replace all `HitlHoldRegistry` calls with
   `HitlHoldRepository` (DB-backed). Inject via FastAPI dependency (`get_db_session`).

4. Add `HitlHoldRecord` dataclass/model to `src/iara/contracts/`.

**Acceptance criteria**:
- HITL hold is registered in `hitl_holds` table when the graph routes to HITL.
- `GET /hitl/pending` returns holds that survive process restart.
- `POST /hitl/{run_id}/approve` resumes the graph correctly.
- `tests/unit/` related to HITL remain green.

**Dependencies**: IARA-006 (stable DB session factory pattern).

---

### IARA-008 · Feature · P1 · Effort: M (4 h)

**Title**: Add `follow_up_queue` table — migration, ORM model, and repository

**Why**:
The follow-up system (EPIC 4) requires a durable queue of scheduled messages. This card
creates the DB foundation before the worker is built. Contract Annex I, section D:
*"queue/scheduler for future message in existing conversation, with opt-out, quiet hours,
max attempts, per-tenant policy, outbox/readback and sanitised logs"*.

**What to implement**:

1. Create migration `0005_add_follow_up_queue.py`:

   ```sql
   CREATE TABLE follow_up_queue (
       id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       tenant_id       UUID NOT NULL REFERENCES tenants(id),
       conversation_id TEXT NOT NULL,
       contact_ref     TEXT NOT NULL,          -- hashed, not raw phone/email
       message_ref     TEXT NOT NULL,          -- SHA-256 hash of message content
       message_length  INT  NOT NULL,
       reason_ref      TEXT NOT NULL,          -- SHA-256 hash of reason
       trigger_at      TIMESTAMPTZ NOT NULL,   -- when to send (UTC)
       status          TEXT NOT NULL DEFAULT 'pending',  -- pending/sent/skipped/failed
       attempt_count   INT NOT NULL DEFAULT 0,
       max_attempts    INT NOT NULL DEFAULT 3,
       opted_out       BOOLEAN NOT NULL DEFAULT FALSE,
       created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
       updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
       sent_at         TIMESTAMPTZ,
       correlation_id  TEXT NOT NULL,
       idempotency_key TEXT NOT NULL UNIQUE
   );
   CREATE INDEX idx_follow_up_queue_trigger ON follow_up_queue (trigger_at)
       WHERE status = 'pending';
   CREATE INDEX idx_follow_up_queue_tenant  ON follow_up_queue (tenant_id, status);
   ```

2. Add `FollowUpQueueItem` SQLAlchemy ORM model to `src/iara/persistence/models.py`.

3. Create `src/iara/persistence/repositories/follow_up_repository.py`:

   ```python
   class FollowUpRepository:
       """Repository for the durable follow-up queue."""

       async def enqueue(self, item: FollowUpQueueItem) -> str:
           """Insert item; return its UUID. Idempotent on idempotency_key."""

       async def fetch_due(
           self,
           now: datetime,
           batch_size: int = 50,
       ) -> list[FollowUpQueueItem]:
           """Fetch pending items with trigger_at <= now, ordered by trigger_at."""

       async def mark_sent(self, item_id: str) -> None: ...
       async def mark_skipped(self, item_id: str, reason: str) -> None: ...
       async def mark_failed(self, item_id: str, error: str) -> None: ...
       async def increment_attempt(self, item_id: str) -> int:
           """Increment attempt_count; return new count."""
       async def mark_opted_out(self, conversation_id: str, tenant_id: str) -> None:
           """Set opted_out=True for all pending items in this conversation."""
   ```

**Acceptance criteria**:
- Migration applies cleanly: `alembic upgrade head`.
- `FollowUpRepository.enqueue()` is idempotent (duplicate `idempotency_key` → no error).
- `fetch_due()` returns only items with `trigger_at <= now` and `status='pending'`.

**Dependencies**: None (pure DB layer, no business logic).

---

### IARA-009 · Feature · P1 · Effort: M (3 h)

**Title**: Add Postgres database seeds for pilot tenant, provider account, and inbox

**Why**:
Contract cl. 6.3: *"migrations/DDL, logical model and minimum seeds so that configurations,
policies, tools/actions, bindings, secrets by reference, follow-ups and catalog/registry of
providers/capabilities are auditable in Postgres"*. Gate G0 requires a working pilot
tenant/account/inbox. Currently the only tenant data is in `InMemoryTenantRepository` hardcoded
in Python — not auditable.

**What to implement**:

Create `src/iara/persistence/seeds/seed_pilot.py`:

```python
"""Minimum-viable seeds for the G0 pilot environment.

Run with: python -m iara.persistence.seeds.seed_pilot

Creates:
- 1 pilot tenant (iara-pilot)
- 1 Chatwoot provider account bound to that tenant
- 1 inbox bound to that account
- 1 published TenantConfig with defaults (suggest_only kanban, disabled follow-up)

All values are read from environment variables so no real credentials are
hardcoded. Idempotent — safe to run multiple times.
"""

PILOT_TENANT_ID  = os.environ["IARA_PILOT_TENANT_ID"]   # UUID
PILOT_ACCOUNT_ID = os.environ["CHATWOOT_ACCOUNT_ID"]
PILOT_INBOX_ID   = os.environ["CHATWOOT_INBOX_ID"]
PILOT_WEBHOOK_KEY = os.environ["IARA_PILOT_WEBHOOK_KEY"]
```

The seed must:
- Insert into `tenants` with `is_active=True` and `webhook_key_hash=sha256(PILOT_WEBHOOK_KEY)`.
- Insert into `provider_accounts` linked to the tenant.
- Insert into `provider_inboxes` linked to the account.
- Insert a published `TenantConfig` with:
  - `kanban_stages: ["new_lead","contacted","nurturing","qualified","proposal_sent","negotiation","won","lost"]`
  - `kanban_mode: "suggest_only"`
  - `follow_up_mode: "disabled"`
  - `active_tools: [<all 20 tool names from AgentToolRegistry>]`

Add `make seed-pilot` target to `Makefile`.

**Acceptance criteria**:
- `make seed-pilot` runs without error in a fresh DB.
- Re-running `make seed-pilot` is idempotent (no duplicate-key errors).
- `GET /config/{PILOT_TENANT_ID}/active` returns the seeded config.

**Dependencies**: IARA-006 (config persistence must work before seeding config).

---

## EPIC 3 — Multi-Provider Outbox Routing (P1)

The outbox drainer only routes to the Chatwoot MCP adapter. Scheduling write commands
(`schedule_appointment`, `cancel_appointment`, `reschedule_appointment`) use
`provider: "google_calendar"` or `provider: "clinicorp"` and have no resolution path.

---

### IARA-010 · Feature · P1 · Effort: M (4 h)

**Title**: Define `SchedulingWriteAdapter` protocol and implement `NullSchedulingWriteAdapter`

**Why**:
Before adding real Google Calendar and Clinicorp write adapters, the protocol must be
established so the outbox drainer can route by provider name and the system degrades
gracefully (null adapter) when a real integration is not configured.
Contract cl. 7.3: *"interface/contract of adapter; fake/stub integration; policy and readback"*.

**What to implement**:

Create `src/iara/provider/scheduling/write_adapter.py`:

```python
from typing import Protocol, runtime_checkable
from iara.contracts.provider import ProviderCommand, ProviderMutationResult, ProviderSecurityContext


@runtime_checkable
class SchedulingWriteAdapter(Protocol):
    """Protocol for providers that execute scheduling write commands.

    Implementations must be idempotent — the outbox drainer may retry.
    All methods must validate tenant_id cross-tenant before writing.
    """

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Execute a scheduling write command and return a result ref.

        Raises:
            FailClosedError: On cross-tenant mismatch or unregistered capability.
            ProviderError: On provider API failure after exhausting retries.
        """
        ...

    async def health_check(self) -> bool:
        """Return True if the provider API is reachable."""
        ...


class NullSchedulingWriteAdapter:
    """No-op scheduling write adapter used when no provider is configured.

    All commands are logged as skipped and return a null result ref.
    Used in development and when the provider credential is absent.
    """

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        logger.warning(
            "scheduling_command_skipped_no_provider",
            capability_name=command.capability_name,
            command_id=str(command.command_id),
        )
        return ProviderMutationResult(
            result_ref=f"skipped:{command.command_id}",
            provider_ref=None,
            readback_hash=None,
        )

    async def health_check(self) -> bool:
        return True
```

Update `src/iara/provider/scheduling/factory.py` to also build write adapters:

```python
def build_scheduling_write_adapter(settings: Settings) -> SchedulingWriteAdapter:
    """Return the appropriate scheduling write adapter based on settings."""
    if settings.scheduling_provider == "google_calendar" and settings.google_calendar_credentials_ref:
        return GoogleCalendarWriteAdapter(credentials_ref=settings.google_calendar_credentials_ref)
    if settings.scheduling_provider == "clinicorp" and settings.clinicorp_api_key_ref:
        return ClinicorpWriteAdapter(api_key_ref=settings.clinicorp_api_key_ref)
    return NullSchedulingWriteAdapter()
```

**Acceptance criteria**:
- `NullSchedulingWriteAdapter` passes the `SchedulingWriteAdapter` `isinstance` check.
- `build_scheduling_write_adapter(settings)` returns `NullSchedulingWriteAdapter` when
  no provider credentials are configured.

**Dependencies**: None.

---

### IARA-011 · Feature · P1 · Effort: L (8 h)

**Title**: Implement `GoogleCalendarWriteAdapter` and `ClinicorpWriteAdapter` with schedule/cancel/reschedule

**Why**:
The tool catalog (`scheduling.py`) already builds `schedule_appointment`, `cancel_appointment`,
and `reschedule_appointment` provider commands. Without write adapters, these commands are
enqueued to the outbox and then dead-lettered because no adapter can execute them. Contract
cl. 6.2 requires scheduling to work end-to-end: *"Create appointment by approved provider,
with idempotency, outbox, readback and fallback/handoff"*.

**What to implement**:

`src/iara/provider/scheduling/google_calendar_write.py`:

```python
class GoogleCalendarWriteAdapter:
    """Google Calendar write adapter for schedule/cancel/reschedule commands.

    Uses service account JWT credentials via credential_ref resolution.
    All writes include an iCalUID derived from command_id for idempotency.
    """

    CAPABILITY_MAP: dict[str, str] = {
        "schedule_appointment":   "events.insert",
        "cancel_appointment":     "events.delete",
        "reschedule_appointment": "events.patch",
    }

    async def execute_command(
        self,
        command: ProviderCommand,
        security_context: ProviderSecurityContext,
    ) -> ProviderMutationResult:
        """Resolve capability, build GCal API payload, POST, return result_ref."""
        self._verify_tenant(security_context)  # INV-02
        capability = self.CAPABILITY_MAP.get(command.capability_name)
        if capability is None:
            raise FailClosedError(f"Unregistered scheduling capability: {command.capability_name!r}")
        token = await self._resolve_credential()
        result = await self._call_gcal_api(capability, command.parameters, token)
        return ProviderMutationResult(
            result_ref=f"gcal:{result['id']}",
            provider_ref=result["id"],
            readback_hash=_hash_result(result),
        )
```

`src/iara/provider/scheduling/clinicorp_write.py`:

```python
class ClinicorpWriteAdapter:
    """Clinicorp write adapter for schedule/cancel/reschedule commands.

    Uses API key credential_ref. Idempotency via external_id = command_id.
    """
    ...
```

Both adapters must:
- Implement `_verify_tenant()` (INV-02 cross-tenant check).
- Use `credential_ref` resolution, never raw values.
- Implement exponential backoff retry on 429/5xx (3 retries, same pattern as `mcp_adapter.py`).
- Return `ProviderMutationResult` with a stable `result_ref` and a readback hash.
- Log with `correlation_id` from `security_context`.

**Acceptance criteria**:
- `GoogleCalendarWriteAdapter.execute_command()` with a mocked HTTPX transport creates an
  event and returns a `result_ref` starting with `"gcal:"`.
- Cross-tenant attempt raises `FailClosedError`.
- Retry logic: first two calls return 429, third succeeds → one result returned.

**Dependencies**: IARA-010 (protocol must exist first).

---

### IARA-012 · Feature · P1 · Effort: M (4 h)

**Title**: Extend `OutboxDrainerWorker` to route commands by `provider` field

**Why**:
`OutboxDrainerWorker` only instantiates `ChatwootMcpAdapter`. Scheduling commands
(`provider: "google_calendar"` / `provider: "clinicorp"`) are dead-lettered immediately.
The drainer must route to the correct adapter based on `ProviderCommand.provider`.

**What to implement**:

In `src/iara/workers/outbox_drainer.py`, refactor the drainer to hold a provider registry:

```python
class OutboxDrainerWorker:
    """Polls provider_command_outbox and routes each command to its provider adapter.

    Each adapter is registered by provider name string. Unknown providers
    trigger FailClosedError and mark the command dead-lettered.
    """

    def __init__(
        self,
        outbox_repo: OutboxRepository,
        adapters: dict[str, ProviderAdapter],   # e.g. {"chatwoot": ..., "google_calendar": ...}
    ) -> None: ...

    async def _dispatch(self, command: ProviderCommand, ctx: ProviderSecurityContext) -> None:
        adapter = self._adapters.get(command.provider)
        if adapter is None:
            raise FailClosedError(f"No adapter registered for provider: {command.provider!r}")
        await adapter.execute_command(command, ctx)
```

In `src/iara/workers/main.py`, build both adapters and pass them:

```python
adapters = {
    "chatwoot": chatwoot_adapter,
    "google_calendar": build_scheduling_write_adapter(settings),
    "clinicorp": build_scheduling_write_adapter(settings),   # factory picks one or Null
}
drainer = OutboxDrainerWorker(outbox_repo=outbox_repo, adapters=adapters)
```

Define `ProviderAdapter` as a `Protocol` in `src/iara/contracts/provider.py` with a single
`execute_command(command, context) -> ProviderMutationResult` method, so both
`ChatwootMcpAdapter` and `SchedulingWriteAdapter` satisfy it without inheritance.

**Acceptance criteria**:
- A `schedule_appointment` command in the outbox with `provider="google_calendar"` routes to
  `GoogleCalendarWriteAdapter`.
- An unknown provider string dead-letters the command with `FailClosedError` in the error field.
- `tests/unit/test_gaps_6_to_10.py` `TestChatwootMcpAdapterRetry` remains green.

**Dependencies**: IARA-001, IARA-010, IARA-011.

---

## EPIC 4 — Follow-Up System (P1 — contractually required, completely absent)

Contract Annex I, section D and Annex II G5: individual follow-up with opt-out, quiet hours,
max attempts, per-tenant policy, outbox/readback. Currently only `build_followup_command()`
exists (a command builder). No worker, no scheduler, no quiet hours, no opt-out, no DB queue.

---

### IARA-013 · Feature · P1 · Effort: L (8 h)

**Title**: Add quiet hours, opt-out, and max-attempts logic to `followup.py` catalog

**Why**:
Contract Annex I, D: *"opt-out, quiet hours, max attempts, sufficient context, per-tenant
policy"*. Annex III: `followup_agendar` requires `"Exige opt-out/quiet hours/tentativas máximas"`.
The current `build_followup_command()` writes none of these fields.

**What to implement**:

In `src/iara/tools/catalog/followup.py`, rewrite `build_followup_command()` to:

1. Accept an optional `TenantConfig` kwarg with `follow_up_policy` field containing:
   ```python
   class FollowUpPolicy(BaseModel):
       max_attempts: int = 3
       quiet_hours_start: int = 22   # 22:00 local time
       quiet_hours_end:   int = 8    # 08:00 local time
       timezone: str = "America/Sao_Paulo"
       opt_out_label: str = "opt_out_followup"
   ```

2. Compute `trigger_at` (when to send) from `arguments["delay_hours"]` and apply quiet hours:
   - If the computed `trigger_at` falls inside the quiet window, advance it to `quiet_hours_end`
     on the next available day.
   - Use `zoneinfo.ZoneInfo(policy.timezone)` — Python 3.9+ stdlib, no extra dependency.

3. Check opt-out labels via `arguments.get("contact_labels", [])`:
   - If the contact has the `opt_out_label` label, return a `{"status": "skipped", "reason": "opted_out"}` dict
     instead of building a command.

4. Return a full `FollowUpQueueEnqueuePayload` dict (not just the outbox command) containing:
   - `trigger_at`, `message_ref`, `reason_ref`, `max_attempts`, `correlation_id`, `idempotency_key`.

5. The calling path (tool executor) will call `FollowUpRepository.enqueue()` with this payload
   instead of going directly to the outbox.

**Acceptance criteria**:
- `trigger_at` computed at 22:30 → adjusted to next day at 08:00 in `America/Sao_Paulo`.
- Contact with `opt_out_followup` label → `status: "skipped"` returned, nothing enqueued.
- `tests/unit/test_catalog_tools.py::TestFollowupHandler` updated and green.

**Dependencies**: IARA-008 (`FollowUpRepository` and table must exist).

---

### IARA-014 · Feature · P1 · Effort: L (8 h)

**Title**: Implement `FollowUpSchedulerWorker` — poll queue, send via outbox, handle retries

**Why**:
Without a worker polling the `follow_up_queue` table, scheduled messages never send.
Contract cl. 6.2: *"Follow-up individual — Programar mensagem futura via outbox/readback"*.
The follow-up outbox command must go through the full outbox → outbox drainer → Chatwoot MCP
path so it has the same idempotency and readback guarantees as all other side effects.

**What to implement**:

Create `src/iara/workers/follow_up_scheduler.py`:

```python
class FollowUpSchedulerWorker:
    """Polls follow_up_queue for due items and enqueues them to provider_command_outbox.

    Runs in its own async loop every POLL_INTERVAL_SECONDS. Each due item is
    converted to a ProviderCommand (capability: 'followup_reengage_conversation')
    and written to the outbox. The outbox drainer handles the actual Chatwoot API
    call and readback — this worker only bridges the follow-up queue to the outbox.

    Skips items that are opted-out or have exceeded max_attempts.
    """

    POLL_INTERVAL_SECONDS: int = 30

    async def run_forever(self) -> None:
        """Main polling loop. Exits on CancelledError."""
        while True:
            try:
                await self._process_due_items()
            except Exception:
                logger.exception("follow_up_scheduler_error")
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    async def _process_due_items(self) -> None:
        async with self._session_factory() as session:
            repo = FollowUpRepository(session)
            items = await repo.fetch_due(now=datetime.utcnow(), batch_size=50)
            for item in items:
                await self._process_item(item, repo, session)

    async def _process_item(
        self,
        item: FollowUpQueueItem,
        repo: FollowUpRepository,
        session: AsyncSession,
    ) -> None:
        """Enqueue one follow-up to the provider_command_outbox."""
        new_count = await repo.increment_attempt(item.id)
        if item.opted_out or new_count > item.max_attempts:
            await repo.mark_skipped(item.id, reason="opted_out_or_max_attempts")
            return
        outbox_repo = OutboxRepository(session)
        await outbox_repo.enqueue(
            ProviderCommand(
                command_id=uuid4(),
                provider="chatwoot",
                capability_name="followup_reengage_conversation",
                parameters={
                    "conversation_id": item.conversation_id,
                    "message_ref": item.message_ref,
                    "message_length": item.message_length,
                    "reason_ref": item.reason_ref,
                    "private": False,
                    "content_type": "text",
                },
                tenant_id=item.tenant_id,
                correlation_id=item.correlation_id,
                idempotency_key=f"followup:{item.id}:attempt:{new_count}",
            ),
            security_context=ProviderSecurityContext(
                tenant_id=item.tenant_id,
                provider_account_ref=_get_account_ref(item.tenant_id),
            ),
        )
        await repo.mark_sent(item.id)
```

Register the worker in `src/iara/workers/main.py` alongside the outbox drainer:
```python
asyncio.gather(outbox_drainer.run_forever(), follow_up_scheduler.run_forever())
```

**Acceptance criteria**:
- Item with `trigger_at = now - 1s`, `status='pending'`, `attempt_count=0` → processed,
  `status='sent'` in DB, one row in `provider_command_outbox`.
- Item with `opted_out=True` → `status='skipped'`, no outbox row.
- Item with `attempt_count >= max_attempts` → `status='skipped'`.
- Worker does not crash on DB connection failure; logs error and retries after
  `POLL_INTERVAL_SECONDS`.

**Dependencies**: IARA-008 (table), IARA-013 (opt-out/quiet hours logic).

---

### IARA-015 · Feature · P1 · Effort: M (4 h)

**Title**: Implement scheduling-confirmation follow-up (T-1h reminder) as automatic post-schedule action

**Why**:
Contract Annex I, section D: *"Scheduling confirmation must be modelled as a Scheduled
Action/Follow-up, with dispatch relative to appointment time (e.g. T-1h), configurable
per tenant"*. When a scheduling command succeeds in the outbox drainer, a follow-up reminder
must be automatically created in `follow_up_queue`.

**What to implement**:

In `src/iara/workers/outbox_drainer.py`, after a successful `schedule_appointment` execution,
create a follow-up reminder:

```python
async def _post_schedule_hook(
    self,
    command: ProviderCommand,
    result: ProviderMutationResult,
    session: AsyncSession,
) -> None:
    """Create T-1h reminder follow-up after successful appointment scheduling.

    The reminder offset is read from the tenant's published TenantConfig
    (follow_up_policy.confirmation_offset_hours, default -1).
    """
    config = await get_kanban_stages(str(command.tenant_id))   # reuse registry
    offset_hours = config.get("confirmation_offset_hours", -1)
    appointment_dt = _parse_appointment_dt(command.parameters)
    if appointment_dt is None:
        logger.warning("no_appointment_dt_in_result", command_id=str(command.command_id))
        return
    trigger_at = appointment_dt + timedelta(hours=offset_hours)
    follow_up_repo = FollowUpRepository(session)
    await follow_up_repo.enqueue(
        FollowUpQueueItem(
            tenant_id=command.tenant_id,
            conversation_id=command.parameters["conversation_id"],
            contact_ref=command.parameters.get("contact_ref", ""),
            message_ref=_hash(f"appointment_reminder:{result.provider_ref}"),
            message_length=80,
            reason_ref=_hash("appointment_confirmation"),
            trigger_at=trigger_at,
            max_attempts=1,
            correlation_id=command.correlation_id,
            idempotency_key=f"confirmation:{command.command_id}",
        )
    )
```

Add `confirmation_offset_hours: int = Field(default=-1, ge=-24, le=0)` to
`FollowUpPolicy` in `TenantConfig`.

**Acceptance criteria**:
- Successful `schedule_appointment` → one `follow_up_queue` row with
  `trigger_at = appointment_datetime - 1h`.
- Idempotent: re-processing the same command does not insert a duplicate.
- Unit test mocks the outbox drainer post-execute hook and verifies the follow-up row.

**Dependencies**: IARA-008, IARA-013, IARA-014.

---

### IARA-016 · Feature · P1 · Effort: M (3 h)

**Title**: Wire follow-up enqueue into `ToolExecutor` for the `followup_schedule` tool path

**Why**:
Currently `ToolExecutor._execute_with_outbox()` sends all write commands to the outbox.
Follow-up scheduling should instead write to `follow_up_queue` (not the outbox) because
the follow-up worker handles the actual sending at `trigger_at`. Without this routing,
follow-up commands will be sent immediately by the outbox drainer instead of being deferred.

**What to implement**:

In `src/iara/tools/executor.py`, add a dedicated path for the follow-up tool:

```python
_FOLLOW_UP_CAPABILITIES = frozenset({"followup_schedule"})

async def execute(self, request: ToolInvocationRequest) -> ToolInvocationResult:
    ...
    if request.tool_name in _FOLLOW_UP_CAPABILITIES:
        return await self._execute_follow_up(request)
    ...

async def _execute_follow_up(self, request: ToolInvocationRequest) -> ToolInvocationResult:
    """Build follow-up payload and enqueue to follow_up_queue (not outbox).

    The follow-up scheduler worker will send the message at trigger_at.
    """
    payload = followup.build_followup_command(
        arguments=request.arguments,
        tenant_id=request.tenant_id,
        conversation_id=request.conversation_id,
        idempotency_key=request.idempotency_key,
        correlation_id=request.correlation_id,
        policy=await self._get_follow_up_policy(request.tenant_id),
    )
    if payload.get("status") == "skipped":
        return ToolInvocationResult(status="skipped", result=payload)
    await self._follow_up_repo.enqueue(FollowUpQueueItem(**payload))
    return ToolInvocationResult(
        status="scheduled",
        result={"trigger_at": payload["trigger_at"].isoformat(), "message_ref": payload["message_ref"]},
    )
```

Inject `FollowUpRepository` into `ToolExecutor` via `build_production_graph()`.

**Acceptance criteria**:
- Calling `followup_schedule` tool → row in `follow_up_queue`, no row in `provider_command_outbox`.
- Calling `send_message` tool → row in `provider_command_outbox` (unchanged behavior).
- `tests/unit/test_executor.py` green.

**Dependencies**: IARA-008, IARA-013.

---

## EPIC 5 — Graph Safety Guards (P2)

---

### IARA-017 · Feature · P2 · Effort: M (4 h)

**Title**: Implement real anti-loop detection in `guardrails_node`

**Why**:
`src/iara/graph/nodes/guardrails.py` has a comment *"stub — real implementation checks
response history"* for the anti-loop check. The agent node has a hard step-count limit
(10 steps) but no semantic loop detection. Contract cl. 6.2: *"Applies anti-loop"*.
A semantic loop (same or very similar response repeated) must be detected and interrupted
before the step limit.

**What to implement**:

In `src/iara/graph/nodes/guardrails.py`, implement `_detect_loop()`:

```python
_SIMILARITY_THRESHOLD = 0.85
_LOOK_BACK_WINDOW = 3   # compare last N assistant messages


def _detect_loop(messages: list[dict[str, Any]]) -> bool:
    """Return True if the last assistant message is near-identical to a recent one.

    Uses normalised Levenshtein ratio via difflib.SequenceMatcher (stdlib, no deps).
    Triggers on _SIMILARITY_THRESHOLD similarity against any of the last
    _LOOK_BACK_WINDOW - 1 prior assistant messages.
    """
    assistant_texts = [
        m["content"] for m in messages
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]
    if len(assistant_texts) < 2:
        return False
    latest = assistant_texts[-1]
    for prior in assistant_texts[-_LOOK_BACK_WINDOW:-1]:
        ratio = difflib.SequenceMatcher(None, prior, latest).ratio()
        if ratio >= _SIMILARITY_THRESHOLD:
            return True
    return False
```

In `guardrails_node`, call `_detect_loop(state["messages"])`:
- If `True`, set `state["loop_detected"] = True` and replace the pending response with a
  safe fallback message: *"Estou com dificuldades em ajudar com isso agora. Um atendente
  humano vai te ajudar em breve."*
- Log with `logger.warning("anti_loop_triggered", ...)`.

Add `loop_detected: bool` field to `GraphState`.

**Acceptance criteria**:
- Three consecutive assistant messages with >85% similarity → `loop_detected=True`, fallback sent.
- Three consecutive messages with <85% similarity → no intervention.
- `tests/unit/test_graph.py` updated with a loop-detection test.

**Dependencies**: None.

---

### IARA-018 · Feature · P2 · Effort: M (4 h)

**Title**: Implement low-confidence guard in `guardrails_node` — route to HITL on uncertain responses

**Why**:
Contract cl. 6.2: *"baixa confiança"* is listed as a guardrail for atendimento conversacional.
No such check exists. When the agent's response signals uncertainty (e.g., "I'm not sure",
"Não tenho certeza", hedging phrases), the graph should optionally route to HITL or return a
human-handoff message rather than delivering uncertain output to the customer.

**What to implement**:

In `src/iara/graph/nodes/guardrails.py`, add `_check_low_confidence()`:

```python
_UNCERTAINTY_PATTERNS = re.compile(
    r"\b(n[aã]o (sei|tenho certeza|estou seguro)|provavelmente|talvez|"
    r"n[aã]o tenho (informa[cç][aã]o|dados)|"
    r"i('m| am) not sure|i don't know|probably|maybe)\b",
    re.IGNORECASE,
)
_LOW_CONFIDENCE_THRESHOLD = 2   # matches in response before flagging


def _check_low_confidence(response: str) -> bool:
    """Return True if the response contains multiple uncertainty signals."""
    return len(_UNCERTAINTY_PATTERNS.findall(response)) >= _LOW_CONFIDENCE_THRESHOLD
```

When `_check_low_confidence(response)` is True AND the tenant's HITL policy has
`hitl_on_low_confidence: True` (new field in `TenantConfig`):
- Set `hitl_requested=True` in the graph state.
- The existing HITL edge routing handles the rest.

When `hitl_on_low_confidence: False` (default):
- Append a human-handoff message to the response instead of triggering HITL.

**Acceptance criteria**:
- Response with 2+ uncertainty phrases + `hitl_on_low_confidence=True` → `hitl_requested=True`
  in state.
- Response with 0 uncertainty phrases → no intervention.
- Default `hitl_on_low_confidence=False` → no HITL, handoff message appended.

**Dependencies**: IARA-007 (HITL DB must be in place before triggering HITL).

---

### IARA-019 · Feature · P2 · Effort: M (3 h)

**Title**: Auto-register HITL holds in the graph node instead of requiring manual `POST /hitl/register`

**Why**:
The current flow requires the caller to explicitly POST to `/hitl/register` after the graph
routes to END with `hitl_requested=True`. This creates a race condition and means holds are
invisible in the DB until manually registered. The graph should register the hold atomically
before suspending.

**What to implement**:

In the graph builder (`src/iara/graph/builder.py`), inject `HitlHoldRepository` into the
context so the HITL edge function can call it:

```python
async def _hitl_guard_node(state: GraphState, config: RunnableConfig) -> GraphState:
    """Register the HITL hold in Postgres before the graph suspends.

    This node fires when hitl_requested=True. It persists the hold so
    operators can see it in GET /hitl/pending without a separate API call.
    """
    if not state.get("hitl_requested"):
        return state
    run_id = config["configurable"].get("thread_id", "unknown")
    await _hitl_repo.register_hold(
        run_id=run_id,
        tenant_id=state["tenant_id"],
        conversation_id=state["conversation_id"],
        reason=state.get("hitl_reason", "policy_triggered"),
        context_snapshot={
            "step_count": state["step_count"],
            "last_tool": state.get("last_tool_invoked"),
        },
    )
    return state
```

Add this node to the graph between the guardrails node and the conditional HITL edge.
Remove the `/hitl/register` endpoint (or keep it as a manual override, clearly documented).

**Acceptance criteria**:
- Graph with `hitl_requested=True` → `hitl_holds` row appears in DB before the graph returns.
- `GET /hitl/pending` lists the hold without any extra API call.
- `POST /hitl/{run_id}/approve` resumes the graph.

**Dependencies**: IARA-007.

---

## EPIC 6 — Documentation Gate Artifacts (P2 — contract deliverables cl. 6.1 + Annex II)

The contract explicitly requires these documentation artifacts as gate acceptance criteria.
Files already exist in `docs/` and should be reviewed and updated, not created from scratch.

---

### IARA-020 · Task · P2 · Effort: M (5 h)

**Title**: Review and update all `docs/` files to reflect the Digi2B MCP refactor and new components

**Why**:
Contract cl. 6.1 last bullet: *"Documentation: logical architecture, API/schema contracts,
tool/action contracts, policy matrix, permissions/HITL matrix, tenant config guide, secrets
guide, runbook, rollback, acceptance checklist"*. Gate G6 criteria: *"rollback documented"*.
Several `docs/` files were written before the MCP Chatwoot refactor and reference outdated
component names, URLs, and architecture.

**What to implement** (file by file):

`docs/architecture.md`:
- Update MCP endpoint pattern to `https://app.digi2b.com/mcp/{account_id}/{slug}`.
- Update auth header to `Api-Access-Token`.
- Add `FollowUpSchedulerWorker` to the worker diagram.
- Add `PostgresTenantRepository` and `HitlHoldRepository` to the persistence diagram.

`docs/configuration.md`:
- Add `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_MCP_SLUG`, `IARA_PILOT_TENANT_ID`,
  `IARA_PILOT_WEBHOOK_KEY` environment variables.
- Document `TenantConfig` schema fields including `FollowUpPolicy` and `confirmation_offset_hours`.
- Document how to run `make seed-pilot`.

`docs/secrets.md`:
- Document `chatwoot_mcp_credential_ref` format and where to store it.
- Document `google_calendar_credentials_ref` and `clinicorp_api_key_ref`.

`docs/runbook.md`:
- Add `FollowUpSchedulerWorker` startup and monitoring section.
- Add `follow_up_queue` table health check query.
- Add HITL hold resolution workflow (operator steps).

`docs/rollback.md`:
- Document config publication rollback via `POST /config/{tenant_id}/rollback/{publication_id}`.
- Document follow-up queue drain-and-pause procedure.

`docs/der.md` (data entity relationship):
- Add `follow_up_queue`, `hitl_holds` tables and their FK relationships.

**Acceptance criteria**:
- Every env var in `.env.example` has a corresponding entry in `docs/configuration.md`.
- `docs/architecture.md` diagram matches the actual running system after EPIC 1–5.
- No references to old component names (e.g., `chatwoot_send_message`, `InMemoryTenantRepository`
  as production path).

**Dependencies**: EPIC 1–5 complete (so docs reflect the final state).

---

### IARA-021 · Task · P2 · Effort: M (4 h)

**Title**: Create `docs/gate_acceptance_checklists.md` with per-gate evidence templates (G0–G6)

**Why**:
Contract cl. 10.2: *"Gates are only considered delivered when accompanied by sanitised evidence,
automated tests, minimum documentation, GitHub access, and functional demo"*. There is no
template to guide what evidence must be produced at each gate. The inspector (Digi2b / Breno
Cocheto) needs a checklist to verify each gate without ambiguity.

**What to implement**:

Create `docs/gate_acceptance_checklists.md` with a section per gate (G0–G6). Each section must
list:
1. Mandatory artefacts (code paths, test files, migration files).
2. Evidence to produce (sanitised test output, Prometheus metric screenshots, log excerpts with
   hashes only).
3. Checklist items matching the contract Annex II criteria verbatim.
4. How to verify fail-closed behaviour (specific test to run, expected output).
5. Common rejection reasons (what the inspector might object to).

Example structure for G2:

```markdown
## G2 — Governed Chatwoot Integration Layer

### Mandatory artefacts
- [ ] `src/iara/provider/chatwoot/mcp_registry.py` — all intents registered
- [ ] `src/iara/provider/chatwoot/mcp_adapter.py` — INV-02 cross-tenant check present
- [ ] `src/iara/provider/chatwoot/fake_mcp.py` — in-memory stub for tests
- [ ] `tests/security/test_mcp_isolation.py` — all tests green
- [ ] `tests/security/test_cross_tenant.py` — all tests green

### Evidence to produce
- Run: `python -m pytest tests/security/ -v --tb=short > evidence/g2_security_tests.txt`
- Verify: no raw Chatwoot tool names (e.g. `conversations_set_labels`) appear in agent prompts
- Screenshot: `GET /metrics` showing `tool_invocations_total` counter

### Checklist (per Annex II)
- [ ] Fail-closed comprovado (cross-tenant attempt → FailClosedError)
- [ ] Account/inbox mismatch bloqueado (test_cross_tenant covers this)
- [ ] Writes inválidos recusados (resolve_intent on unknown intent → denied)
- [ ] Evidências sanitizadas (no account IDs, tokens, or phone numbers in evidence files)
```

**Acceptance criteria**:
- Each gate section has at least 5 checklist items.
- All items reference specific test files or commands that can be run by a third-party inspector.
- The document can be used as a literal sign-off sheet (checkboxes).

**Dependencies**: IARA-020 (docs must be accurate first).

---

### IARA-022 · Task · P2 · Effort: S (3 h)

**Title**: Create `docs/g0_kickoff_template.md` — formal G0 decision document for Digi2b

**Why**:
Contract cl. 10.1 and Annex I, section E: G0 requires formal documentation of 8+ decisions
(provider, kanban mode, follow-up mode, pilot tenants, HITL approvers, data matrix, sandbox
environment, write modes). Contract Annex II: G0 criterion is *"scope/non-scope confirmed
with consultation to Breno Cocheto"*. No template exists to structure this sign-off.

**What to implement**:

Create `docs/g0_kickoff_template.md` with sections for each mandatory G0 decision from
Annex I, section E. The template must be fillable (Markdown checkboxes + blank fields):

```markdown
# G0 Kickoff Technical Document — IAra Multi-Tenant

**Project**: IAra Multi-Tenant  
**Date**: _______________  
**SymCorp representative**: Yan Vianna Sym  
**Digi2b TI representative**: Breno Cocheto (breno@digi2b.com)  
**Status**: ☐ Draft  ☐ Under review  ☐ Signed off

## 1. Scheduling Provider Decision
- [ ] Google Calendar (credentials ref: _______________)
- [ ] Clinicorp (API key ref: _______________)
- [ ] Custom adapter (spec attached: _______________)

**Decision**: _______________ **Approved by**: _______________

## 2. Kanban Initial Mode
- [ ] suggest_only (default — no writes to Chatwoot)
- [ ] write_sandbox (writes to sandbox account only)
- [ ] write_confirmed (full writes with HITL gate)

**Decision**: _______________ **Approved by**: _______________

[... continue for all 8 Annex I-E items ...]

## Signatures
**SymCorp**: _______________ **Date**: _______________  
**Digi2b TI**: _______________ **Date**: _______________
```

**Acceptance criteria**:
- All 8 mandatory G0 decisions from Annex I, section E are covered.
- Document explicitly references that Breno Cocheto must sign off on G0 and G1 technical items.
- Includes a "Non-scope confirmation" section listing what is OUT of scope per cl. 14.2.

**Dependencies**: None.

---

## Effort Summary

| Epic | Cards | Total estimated effort |
|------|-------|----------------------|
| EPIC 1 — Critical Production Bugs | 5 | ~19 h |
| EPIC 2 — Persistence Layer Completion | 4 | ~20 h |
| EPIC 3 — Multi-Provider Outbox Routing | 3 | ~16 h |
| EPIC 4 — Follow-Up System | 4 | ~23 h |
| EPIC 5 — Graph Safety Guards | 3 | ~11 h |
| EPIC 6 — Documentation Gate Artifacts | 3 | ~12 h |
| **Total** | **22** | **~101 h** |

## Implementation order (strict)

```
IARA-001 → IARA-002 → IARA-003 → IARA-004 → IARA-005  (EPIC 1 — all parallel-safe)
    ↓
IARA-006 → IARA-007 → IARA-008 → IARA-009              (EPIC 2 — sequential within epic)
    ↓
IARA-010 → IARA-011 → IARA-012                         (EPIC 3 — sequential)
    ↓
IARA-013 → IARA-014 → IARA-015 → IARA-016              (EPIC 4 — sequential)
    ↓
IARA-017 → IARA-018 → IARA-019                         (EPIC 5 — sequential)
    ↓
IARA-020 → IARA-021 → IARA-022                         (EPIC 6 — after code is stable)
```

EPIC 1 cards (IARA-001 through IARA-005) are independent of each other and can be implemented
in parallel if multiple agents are available. All other epics must be implemented in order.

## Quality gates (apply to every card)

1. `python -m ruff check src/ tests/` — zero errors.
2. `python -m pytest tests/unit tests/security -x -q` — all green.
3. No `# type: ignore` added without a comment explaining why.
4. No raw credentials, tokens, phone numbers, or account IDs in any committed file.
5. Every new public function and class has an English docstring explaining WHAT it does,
   its parameters, and what it raises. No docstrings explaining WHY (that goes in commit messages).
6. Every new DB-interacting function uses `async with session.begin()` or relies on the
   caller's transaction — no auto-commit outside the session factory.
