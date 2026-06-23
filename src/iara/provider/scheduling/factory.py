"""Scheduling adapter factory.

Selects and configures the appropriate scheduling backend based on settings.
Priority: Clinicorp > Google Calendar > Null (when neither is configured).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from iara.observability.logging import get_logger
from iara.provider.scheduling.null_adapter import NullSchedulingAdapter
from iara.provider.scheduling.protocol import SchedulingAdapter

if TYPE_CHECKING:
    from iara.config.settings import Settings

logger = get_logger(__name__)


def _resolve_credential(credential_ref: str) -> str:
    """Resolve a credential reference to its actual value.

    If the ref is a ``secret://`` path, look for an env var with the
    path converted to upper-case (e.g. ``secret://clinicorp/api_key``
    → env var ``CLINICORP_API_KEY``).

    Returns the ref itself when no mapping is found (useful in dev).
    """
    if not credential_ref.startswith("secret://"):
        return credential_ref

    path = credential_ref[len("secret://") :]
    env_key = path.replace("/", "_").upper()
    return os.environ.get(env_key, credential_ref)


def build_google_calendar_write_adapter(settings: Settings) -> Any:
    """Build a Google Calendar write adapter, or Null if not configured.

    Args:
        settings: Application settings.

    Returns:
        GoogleCalendarWriteAdapter or NullSchedulingWriteAdapter.
    """
    from iara.provider.scheduling.write_adapter import NullSchedulingWriteAdapter

    if settings.google_calendar_enabled:
        cred = _resolve_credential(settings.google_calendar_credential_ref)
        if not cred.startswith("secret://"):
            from iara.provider.scheduling.google_calendar_write import GoogleCalendarWriteAdapter

            logger.info("scheduling_write_adapter_google_calendar")
            return GoogleCalendarWriteAdapter(
                credentials_ref=settings.google_calendar_credential_ref
            )

    logger.info("scheduling_write_adapter_google_calendar_null_fallback")
    return NullSchedulingWriteAdapter()


def build_clinicorp_write_adapter(settings: Settings) -> Any:
    """Build a Clinicorp write adapter, or Null if not configured.

    Args:
        settings: Application settings.

    Returns:
        ClinicorpWriteAdapter or NullSchedulingWriteAdapter.
    """
    from iara.provider.scheduling.write_adapter import NullSchedulingWriteAdapter

    if settings.clinicorp_enabled:
        cred = _resolve_credential(settings.clinicorp_credential_ref)
        if not cred.startswith("secret://"):
            from iara.provider.scheduling.clinicorp_write import ClinicorpWriteAdapter

            logger.info("scheduling_write_adapter_clinicorp")
            return ClinicorpWriteAdapter(
                api_key_ref=settings.clinicorp_credential_ref,
                base_url=settings.clinicorp_base_url,
            )

    logger.info("scheduling_write_adapter_clinicorp_null_fallback")
    return NullSchedulingWriteAdapter()


def build_scheduling_write_adapter(settings: Settings) -> Any:
    """Build the highest-priority scheduling write adapter available.

    Priority: Clinicorp > Google Calendar > Null.
    Use the provider-specific builders when building an ``adapters`` dict
    for multi-provider outbox routing.

    Args:
        settings: Application settings.

    Returns:
        SchedulingWriteAdapter: The configured write adapter.
    """
    if settings.clinicorp_enabled:
        cred = _resolve_credential(settings.clinicorp_credential_ref)
        if not cred.startswith("secret://"):
            return build_clinicorp_write_adapter(settings)

    if settings.google_calendar_enabled:
        cred = _resolve_credential(settings.google_calendar_credential_ref)
        if not cred.startswith("secret://"):
            return build_google_calendar_write_adapter(settings)

    from iara.provider.scheduling.write_adapter import NullSchedulingWriteAdapter

    logger.info("scheduling_write_adapter_null_fallback")
    return NullSchedulingWriteAdapter()


def build_scheduling_adapter(settings: Settings) -> SchedulingAdapter:
    """Build the best available scheduling adapter from application settings.

    Preference order:
    1. Clinicorp (if ``clinicorp_enabled=True`` and key resolves)
    2. Google Calendar (if ``google_calendar_enabled=True`` and SA JSON resolves)
    3. NullSchedulingAdapter (graceful fallback)

    Args:
        settings: Application settings.

    Returns:
        SchedulingAdapter: The configured adapter.
    """
    if settings.clinicorp_enabled:
        adapter = _try_clinicorp(settings)
        if adapter is not None:
            return adapter

    if settings.google_calendar_enabled:
        adapter = _try_google_calendar(settings)
        if adapter is not None:
            return adapter

    logger.info("scheduling_adapter_null_fallback")
    return NullSchedulingAdapter()


def _try_clinicorp(settings: Settings) -> SchedulingAdapter | None:
    """Attempt to build the Clinicorp adapter."""
    from iara.provider.scheduling.clinicorp import ClinicorpAdapter

    api_key = _resolve_credential(settings.clinicorp_credential_ref)
    if api_key.startswith("secret://"):
        logger.warning("clinicorp_credential_unresolved_using_null")
        return None

    adapter: SchedulingAdapter = ClinicorpAdapter(
        base_url=settings.clinicorp_base_url,
        api_key=api_key,
    )
    logger.info("scheduling_adapter_clinicorp")
    return adapter


def _try_google_calendar(settings: Settings) -> SchedulingAdapter | None:
    """Attempt to build the Google Calendar adapter."""
    from iara.provider.scheduling.google_calendar import GoogleCalendarAdapter

    sa_json_raw = _resolve_credential(settings.google_calendar_credential_ref)
    if sa_json_raw.startswith("secret://"):
        logger.warning("google_calendar_credential_unresolved_using_null")
        return None

    try:
        sa_json: dict[str, Any] = json.loads(sa_json_raw)
    except json.JSONDecodeError:
        logger.warning("google_calendar_credential_invalid_json_using_null")
        return None

    adapter: SchedulingAdapter = GoogleCalendarAdapter(service_account_json=sa_json)
    logger.info("scheduling_adapter_google_calendar")
    return adapter
