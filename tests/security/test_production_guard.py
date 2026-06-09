"""Security tests — production guard invariant (INV-07).

Verifies that no code path may target real production tenants or accounts
without an explicit ``IARA_PRODUCTION_AUTHORIZED=true`` configuration flag.
Default configuration must point only at sandbox/synthetic data.

Any call to ``assert_production_authorized()`` without the flag must raise
``ProductionBlockedError`` — there is no fallback or bypass.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iara.contracts.errors import ProductionBlockedError
from iara.security.guards import assert_production_authorized


@pytest.mark.unit
@pytest.mark.security
class TestProductionGuardDefaultBlocked:
    """By default, production access must be blocked."""

    def test_raises_production_blocked_when_flag_false(self) -> None:
        """assert_production_authorized() raises ProductionBlockedError by default."""
        mock_settings = MagicMock()
        mock_settings.iara_production_authorized = False

        with (
            patch("iara.config.settings.get_settings", return_value=mock_settings),
            pytest.raises(ProductionBlockedError),
        ):
            assert_production_authorized()

    def test_production_blocked_error_has_correct_code(self) -> None:
        """ProductionBlockedError must carry the PRODUCTION_BLOCKED code."""
        err = ProductionBlockedError()
        assert err.code == "PRODUCTION_BLOCKED"
        assert "IARA_PRODUCTION_AUTHORIZED" in err.message

    def test_passes_when_flag_is_true(self) -> None:
        """assert_production_authorized() must not raise when flag is explicitly True."""
        mock_settings = MagicMock()
        mock_settings.iara_production_authorized = True

        with patch("iara.config.settings.get_settings", return_value=mock_settings):
            assert_production_authorized()  # Should not raise

    def test_blocked_even_when_env_is_production_without_flag(self) -> None:
        """Production env alone is not sufficient — flag must also be True."""
        mock_settings = MagicMock()
        mock_settings.iara_production_authorized = False
        mock_settings.iara_env = "production"

        with (
            patch("iara.config.settings.get_settings", return_value=mock_settings),
            pytest.raises(ProductionBlockedError),
        ):
            assert_production_authorized()


@pytest.mark.unit
@pytest.mark.security
class TestSettingsProductionProperty:
    """Settings.is_production must require BOTH production env AND authorized flag."""

    def test_is_production_false_by_default(self) -> None:
        """Default settings must not be considered production."""
        from iara.config.settings import Settings

        settings = Settings()
        assert settings.is_production is False

    def test_is_production_requires_both_env_and_flag(self) -> None:
        """is_production=True requires env=production AND iara_production_authorized=True."""
        from iara.config.settings import Environment, Settings

        settings_prod_no_flag = Settings(
            iara_env=Environment.PRODUCTION,
            iara_production_authorized=False,
        )
        assert settings_prod_no_flag.is_production is False

        settings_flag_no_prod = Settings(
            iara_env=Environment.DEVELOPMENT,
            iara_production_authorized=True,
        )
        assert settings_flag_no_prod.is_production is False

        settings_both = Settings(
            iara_env=Environment.PRODUCTION,
            iara_production_authorized=True,
        )
        assert settings_both.is_production is True

    def test_is_development_true_by_default(self) -> None:
        """Default environment must be development (sandbox-safe)."""
        from iara.config.settings import Settings

        settings = Settings()
        assert settings.is_development is True


@pytest.mark.unit
@pytest.mark.security
class TestSyntheticTenantContextIsSandbox:
    """The synthetic test context used across the test suite must be SANDBOX status."""

    def test_synthetic_tenant_is_not_production(self, synthetic_tenant_context: object) -> None:
        """The shared test fixture must have SANDBOX status (never ACTIVE/PRODUCTION)."""
        from iara.contracts.tenancy import TenantStatus

        ctx = synthetic_tenant_context  # type: ignore[assignment]
        assert ctx.status == TenantStatus.SANDBOX  # type: ignore[union-attr]

    def test_synthetic_tenant_assert_active_passes(self, synthetic_tenant_context: object) -> None:
        """SANDBOX status is treated as active-equivalent for test purposes."""
        ctx = synthetic_tenant_context  # type: ignore[assignment]
        ctx.assert_active()  # type: ignore[union-attr]  # Should not raise
