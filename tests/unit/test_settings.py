"""Unit tests for Settings / configuration module.

Tests use environment variable overrides — no real external services touched.
"""

from __future__ import annotations

import pytest

from iara.config.settings import Settings


@pytest.mark.unit
class TestSettings:
    """Tests for Settings model and derived properties."""

    def test_default_settings_are_development(self) -> None:
        """Default environment must be development."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert settings.iara_env == "development"

    def test_is_production_requires_authorized_flag(self) -> None:
        """is_production must be False unless env=production AND authorized=true."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            iara_env="production",  # type: ignore[call-arg]
            iara_production_authorized=False,
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert settings.is_production is False

    def test_is_production_true_when_both_flags_set(self) -> None:
        """is_production must be True when env=production AND authorized=true."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            iara_env="production",  # type: ignore[call-arg]
            iara_production_authorized=True,
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert settings.is_production is True

    def test_is_development_true_for_default_env(self) -> None:
        """is_development must be True when env=development."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert settings.is_development is True

    def test_kanban_mode_default_suggest_only(self) -> None:
        """Default kanban mode must be suggest_only per INV-06."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert "suggest" in settings.iara_kanban_default_mode.value.lower()

    def test_campaign_mode_default_draft_only(self) -> None:
        """Default campaign mode must be draft_only per INV-06."""
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@localhost/db",
            rabbitmq_url="amqp://u:p@localhost/v",
        )
        assert "draft" in settings.iara_campaign_default_mode.value.lower()
