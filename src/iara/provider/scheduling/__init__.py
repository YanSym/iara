"""Scheduling provider adapters — Google Calendar, Clinicorp, and null fallback."""

from iara.provider.scheduling.factory import build_scheduling_adapter
from iara.provider.scheduling.protocol import SchedulingAdapter

__all__ = ["SchedulingAdapter", "build_scheduling_adapter"]
