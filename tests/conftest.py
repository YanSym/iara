"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from iara.contracts.tenancy import TenantContext, TenantStatus
from iara.provider.chatwoot.fake_mcp import FakeChatwootAdapter
from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry
from iara.tenancy.resolver import InMemoryTenantRepository, TenantResolver

SYNTHETIC_TENANT_KEY = "test_tenant_001"
SYNTHETIC_ACCOUNT_ID = "11111"
SYNTHETIC_TENANT_ID = "12345678-1234-5678-1234-567812345678"


@pytest.fixture
def synthetic_tenant_id() -> str:
    """Return a consistent synthetic tenant UUID string."""
    return SYNTHETIC_TENANT_ID


@pytest.fixture
def synthetic_account_id() -> str:
    """Return a consistent synthetic account ID."""
    return SYNTHETIC_ACCOUNT_ID


@pytest.fixture
def synthetic_tenant_context(synthetic_tenant_id: str, synthetic_account_id: str) -> TenantContext:
    """Return a synthetic TenantContext for testing."""
    return TenantContext(
        tenant_id=uuid.UUID(synthetic_tenant_id),
        tenant_key=SYNTHETIC_TENANT_KEY,
        tenant_name="Synthetic Test Tenant",
        status=TenantStatus.SANDBOX,
        provider_account_id=synthetic_account_id,
        provider="chatwoot",
        resolved_at=datetime.now(UTC),
    )


@pytest.fixture
def in_memory_tenant_repo(
    synthetic_tenant_id: str, synthetic_account_id: str
) -> InMemoryTenantRepository:
    """Return an in-memory tenant repository pre-loaded with a synthetic tenant."""
    repo = InMemoryTenantRepository()
    repo.register(
        SYNTHETIC_TENANT_KEY,
        {
            "tenant_id": synthetic_tenant_id,
            "name": "Synthetic Test Tenant",
            "status": "sandbox",
            "provider": "chatwoot",
            "provider_account_id": synthetic_account_id,
        },
    )
    return repo


@pytest.fixture
def tenant_resolver(in_memory_tenant_repo: InMemoryTenantRepository) -> TenantResolver:
    """Return a TenantResolver backed by the in-memory repository."""
    return TenantResolver(repository=in_memory_tenant_repo, cache_ttl_seconds=60)


@pytest.fixture
def chatwoot_registry(synthetic_tenant_id: str, synthetic_account_id: str) -> ChatwootMcpRegistry:
    """Return a ChatwootMcpRegistry for the synthetic tenant."""
    return ChatwootMcpRegistry(
        tenant_id=synthetic_tenant_id,
        account_id_ref=synthetic_account_id,
    )


@pytest.fixture
def fake_chatwoot_adapter(chatwoot_registry: ChatwootMcpRegistry) -> FakeChatwootAdapter:
    """Return a FakeChatwootAdapter for testing."""
    return FakeChatwootAdapter(registry=chatwoot_registry)
