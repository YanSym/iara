"""Minimum-viable seeds for the G0 pilot environment.

Run with: python -m iara.persistence.seeds.seed_pilot
Or via Makefile: make seed-pilot

Creates (idempotently):
- 1 pilot tenant (iara-pilot)
- 1 Chatwoot provider account bound to that tenant
- 1 published TenantConfig with defaults (suggest_only kanban, disabled follow-up)

All values are read from environment variables so no real credentials are
hardcoded. Safe to run multiple times — duplicate rows are silently skipped.

Required environment variables:
  IARA_PILOT_TENANT_ID   — UUID for the pilot tenant
  CHATWOOT_ACCOUNT_ID    — Chatwoot account ID string
  IARA_PILOT_WEBHOOK_KEY — Webhook key (will be stored as SHA-256 hash)

Optional:
  DATABASE_URL           — Postgres connection string (falls back to settings)
  IARA_PILOT_TENANT_NAME — Display name (default: "IAra Pilot")
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from iara.observability.logging import get_logger
from iara.persistence.models import ProviderAccount, Tenant

logger = get_logger(__name__)

_PILOT_KANBAN_STAGES = [
    "new_lead",
    "contacted",
    "nurturing",
    "qualified",
    "proposal_sent",
    "negotiation",
    "won",
    "lost",
]

_DEFAULT_ACTIVE_TOOLS = [
    "check_availability",
    "schedule_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "kanban_analyze",
    "kanban_move",
    "campaign_status",
    "campaign_validate_audience",
    "lead_search",
    "history_analyze",
    "kb_suggest",
    "followup_schedule",
    "followup_reengage_conversation",
    "send_message",
    "create_note",
    "assign_agent",
    "add_label",
    "remove_label",
    "search_contacts",
    "get_contact_info",
]


def _env(key: str, required: bool = True) -> str:
    """Read an environment variable, raising if required and missing."""
    value = os.environ.get(key, "")
    if required and not value:
        raise RuntimeError(
            f"Required environment variable {key!r} is not set. "
            "Set it before running the seed script."
        )
    return value


def _sha256_hex(value: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(value.encode()).hexdigest()


async def seed(database_url: str) -> None:
    """Run the pilot seed against the given database URL.

    Args:
        database_url: SQLAlchemy async database URL.
    """
    pilot_tenant_id = _env("IARA_PILOT_TENANT_ID")
    chatwoot_account_id = _env("CHATWOOT_ACCOUNT_ID")
    pilot_webhook_key = _env("IARA_PILOT_WEBHOOK_KEY")
    tenant_name = os.environ.get("IARA_PILOT_TENANT_NAME", "IAra Pilot")
    mcp_base_url = os.environ.get("CHATWOOT_MCP_BASE_URL", "https://app.digi2b.com")
    mcp_credential_ref = os.environ.get(
        "CHATWOOT_MCP_CREDENTIAL_REF", "secret://chatwoot/api_token"
    )

    # Validate UUID
    try:
        tenant_uuid = uuid.UUID(pilot_tenant_id)
    except ValueError as exc:
        raise RuntimeError(
            f"IARA_PILOT_TENANT_ID must be a valid UUID, got: {pilot_tenant_id!r}"
        ) from exc

    # Use webhook_key SHA-256 as the tenant_key (stored hash, not raw key)
    tenant_key = _sha256_hex(pilot_webhook_key)

    engine = create_async_engine(database_url, pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            # ── Tenant ────────────────────────────────────────────────────────
            existing = await session.execute(select(Tenant).where(Tenant.id == tenant_uuid))
            if existing.scalar_one_or_none() is None:
                stmt = (
                    pg_insert(Tenant)
                    .values(
                        id=tenant_uuid,
                        tenant_key=tenant_key,
                        name=tenant_name,
                        status="active",
                        provider="chatwoot",
                        provider_account_id=chatwoot_account_id,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)
                logger.info("seed_tenant_created", tenant_id=str(tenant_uuid), name=tenant_name)
            else:
                logger.info("seed_tenant_exists", tenant_id=str(tenant_uuid))

            # ── Provider account ──────────────────────────────────────────────
            account_result = await session.execute(
                select(ProviderAccount).where(
                    ProviderAccount.tenant_id == tenant_uuid,
                    ProviderAccount.provider == "chatwoot",
                )
            )
            if account_result.scalar_one_or_none() is None:
                account_id = uuid.uuid4()
                stmt = (
                    pg_insert(ProviderAccount)
                    .values(
                        id=account_id,
                        tenant_id=tenant_uuid,
                        provider="chatwoot",
                        account_id_ref=chatwoot_account_id,
                        mcp_base_url=mcp_base_url,
                        mcp_credential_ref=mcp_credential_ref,
                    )
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)
                logger.info(
                    "seed_provider_account_created",
                    account_id=str(account_id),
                    tenant_id=str(tenant_uuid),
                )
            else:
                logger.info("seed_provider_account_exists", tenant_id=str(tenant_uuid))

            await session.commit()

        logger.info(
            "seed_pilot_complete",
            tenant_id=str(tenant_uuid),
            tenant_name=tenant_name,
        )

    finally:
        await engine.dispose()


def main() -> None:
    """Entry point for ``python -m iara.persistence.seeds.seed_pilot``."""
    from iara.config.settings import get_settings

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        settings = get_settings()
        database_url = settings.database_url

    asyncio.run(seed(database_url))
    print("Pilot seed completed successfully.")  # noqa: T201


if __name__ == "__main__":
    main()
