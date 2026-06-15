"""Application settings loaded from environment variables.

All secrets are referenced by a ``*_ref`` field that points to the secret store path.
The actual secret value is never stored in these settings; it is resolved at call time
through the secret resolution layer.

Example::

    settings = get_settings()
    db_url = settings.database_url
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment identifiers."""

    DEVELOPMENT = "development"
    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"


class KanbanMode(StrEnum):
    """Kanban write permission modes."""

    SUGGEST_ONLY = "suggest_only"
    WRITE_SANDBOX = "write_sandbox"
    WRITE_CONFIRMED = "write_confirmed"


class CampaignMode(StrEnum):
    """Campaign execution modes, from safest to most permissive."""

    DRAFT_ONLY = "draft_only"
    DRY_RUN = "dry_run"
    SANDBOX = "sandbox"
    APPROVED_SEND = "approved_send"


class LogFormat(StrEnum):
    """Log output format."""

    JSON = "json"
    CONSOLE = "console"


class LlmProvider(StrEnum):
    """LLM provider selector."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    """IAra runtime settings.

    All fields are loaded from environment variables. Secret values are referenced
    by a ``_ref`` suffix field and resolved at runtime through the secret store.

    Raises:
        ValidationError: If required fields are missing or invalid.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ───────────────────────────────────────────────────────────
    iara_env: Environment = Field(
        default=Environment.DEVELOPMENT, description="Deployment environment"
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://iara:iara_dev@localhost:5432/iara_dev",
        description="Async SQLAlchemy database URL",
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)
    database_echo: bool = Field(default=False)

    # ── RabbitMQ ─────────────────────────────────────────────────────────────
    rabbitmq_url: str = Field(
        default="amqp://iara:iara_dev@localhost:5672/iara",
        description="AMQP connection URL",
    )
    rabbitmq_prefetch_count: int = Field(default=10, ge=1, le=1000)
    rabbitmq_reconnect_delay_seconds: int = Field(default=5, ge=1, le=300)

    # ── LLM — provider selector ───────────────────────────────────────────────
    llm_provider: LlmProvider = Field(
        default=LlmProvider.ANTHROPIC,
        description="LLM provider to use: 'anthropic' or 'openai'",
    )

    # ── LLM — Anthropic ───────────────────────────────────────────────────────
    anthropic_api_key_ref: str = Field(
        default="secret://anthropic/api_key",
        description="Secret reference path for the Anthropic API key",
    )
    anthropic_api_key: str | None = Field(
        default=None,
        description="Direct API key for local dev only — never use in production",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="Anthropic model identifier (env: ANTHROPIC_MODEL)",
    )
    max_tokens: int = Field(default=4096, ge=1, le=200000)

    # ── LLM — OpenAI ─────────────────────────────────────────────────────────
    openai_api_key_ref: str = Field(
        default="secret://openai/api_key",
        description="Secret reference path for the OpenAI API key",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="Direct API key for local dev only — never use in production",
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Default OpenAI model identifier (env: OPENAI_MODEL)",
    )

    # ── FastAPI / HTTP ────────────────────────────────────────────────────────
    iara_host: str = Field(default="0.0.0.0")
    iara_port: int = Field(default=8000, ge=1, le=65535)
    iara_workers: int = Field(default=1, ge=1, le=32)
    iara_reload: bool = Field(default=False)
    # Declared as Any so pydantic-settings doesn't try json.loads() on a
    # comma-separated string like "http://a:3000,http://b:8000".
    # parse_origins validator converts it to list[str] before model validation.
    iara_allowed_origins: Any = Field(default_factory=list)
    iara_webhook_secret_ref: str = Field(default="secret://iara/webhook_hmac_key")

    # ── Tenant ────────────────────────────────────────────────────────────────
    iara_tenant_cache_ttl_seconds: int = Field(default=60, ge=0)
    iara_tenant_max_cache_size: int = Field(default=1000, ge=1)

    # ── Chatwoot MCP ──────────────────────────────────────────────────────────
    # Base URL of the Chatwoot instance (no trailing slash).
    # Full MCP endpoint is composed as: {base_url}/mcp/{account_id}/{mcp_slug}
    chatwoot_mcp_base_url: str = Field(default="https://app.digi2b.com")
    # Numeric Chatwoot account ID (e.g. "59" for suporte, "42" for oral-unic).
    chatwoot_account_id: str = Field(default="", description="Chatwoot account ID (numeric string)")
    # MCP server slug configured in the Chatwoot instance (e.g. 'mcp-suporte').
    chatwoot_mcp_slug: str = Field(default="", description="MCP server slug (per-tenant)")
    # Api-Access-Token for the MCP server (header: Api-Access-Token).
    chatwoot_mcp_credential_ref: str = Field(default="secret://chatwoot/mcp_token")
    chatwoot_mcp_timeout_seconds: int = Field(default=30, ge=1, le=300)
    chatwoot_mcp_max_retries: int = Field(default=3, ge=0, le=10)

    # ── Google Calendar ───────────────────────────────────────────────────────
    google_calendar_enabled: bool = Field(default=False)
    google_calendar_credential_ref: str = Field(default="secret://google/service_account_json")

    # ── Clinicorp ─────────────────────────────────────────────────────────────
    clinicorp_enabled: bool = Field(default=False)
    clinicorp_base_url: str = Field(default="https://api.clinicorp.com")
    clinicorp_credential_ref: str = Field(default="secret://clinicorp/api_key")

    # ── Debounce & lease ─────────────────────────────────────────────────────
    iara_debounce_seconds: int = Field(default=3, ge=0, le=3600)
    iara_lease_ttl_seconds: int = Field(default=300, ge=10, le=7200)
    iara_lease_refresh_interval_seconds: int = Field(default=60, ge=5, le=600)

    # ── Security ─────────────────────────────────────────────────────────────
    iara_redact_sensitive_fields: bool = Field(default=True)
    iara_audit_log_level: str = Field(default="INFO")
    iara_outbox_max_retries: int = Field(default=5, ge=0, le=100)
    iara_outbox_retry_delay_seconds: int = Field(default=30, ge=1, le=3600)

    # ── Media ─────────────────────────────────────────────────────────────────
    iara_media_max_size_mb: int = Field(default=50, ge=1, le=500)
    iara_audio_transcription_enabled: bool = Field(default=True)
    iara_vision_enabled: bool = Field(default=False)
    iara_document_extraction_enabled: bool = Field(default=True)

    # ── Memory ────────────────────────────────────────────────────────────────
    iara_memory_enabled: bool = Field(default=False)
    iara_memory_ttl_days: int = Field(default=90, ge=1, le=3650)
    iara_memory_namespace: str = Field(default="default")

    # ── Campaign & kanban defaults ────────────────────────────────────────────
    iara_kanban_default_mode: KanbanMode = Field(default=KanbanMode.SUGGEST_ONLY)
    iara_campaign_default_mode: CampaignMode = Field(default=CampaignMode.DRAFT_ONLY)

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: LogFormat = Field(default=LogFormat.JSON)
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    otel_service_name: str = Field(default="iara-runtime")

    # ── Production guard ──────────────────────────────────────────────────────
    iara_production_authorized: bool = Field(
        default=False,
        description="Must be explicitly set to True with Digi2B authorization to access production",
    )

    @field_validator("iara_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: Any) -> list[str]:
        """Parse comma-separated origins string into a list."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        if isinstance(v, list):
            return [str(o) for o in v if str(o).strip()]
        return []

    @property
    def is_production(self) -> bool:
        """Return True only if env is production AND explicitly authorized."""
        return self.iara_env == Environment.PRODUCTION and self.iara_production_authorized

    @property
    def is_development(self) -> bool:
        """Return True for development environment."""
        return self.iara_env == Environment.DEVELOPMENT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    Returns:
        Settings: The application settings loaded from environment.
    """
    return Settings()
