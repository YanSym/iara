"""TenantConfig schema — structured config accepted by POST /config/{tenant_id}/draft.

This is the contract between the external backend (e.g. Breno's system) and
the IAra runtime. All fields are optional with safe defaults so the backend
can send partial updates.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    """Agent persona settings."""

    name: str = Field(default="IAra", description="Agent display name")
    tone: str = Field(
        default="professional",
        description="Conversation tone: professional | friendly | formal",
    )
    language: str = Field(default="pt-BR", description="BCP-47 language tag")
    system_prompt_addendum: str | None = Field(
        default=None,
        description="Extra instructions appended to the system prompt verbatim",
    )


class DaySchedule(BaseModel):
    """Operating hours for a single weekday."""

    start: str = Field(description="HH:MM — opening time")
    end: str = Field(description="HH:MM — closing time")


class BusinessHoursConfig(BaseModel):
    """Weekly business hours schedule."""

    timezone: str = Field(
        default="America/Sao_Paulo",
        description="IANA timezone (e.g. 'America/Sao_Paulo')",
    )
    monday: DaySchedule | None = None
    tuesday: DaySchedule | None = None
    wednesday: DaySchedule | None = None
    thursday: DaySchedule | None = None
    friday: DaySchedule | None = None
    saturday: DaySchedule | None = None
    sunday: DaySchedule | None = None


class TenantConfig(BaseModel):
    """Full tenant runtime configuration.

    Sent by the external backend to configure the agent's persona, schedule,
    Kanban pipeline stages and which tools are active for this tenant.

    All fields are optional — omitting a field keeps the current published value.
    """

    version_tag: str = Field(description="Human-readable version label (e.g. 'v1.2-hotfix')")
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    business_hours: BusinessHoursConfig = Field(default_factory=BusinessHoursConfig)
    kanban_stages: list[str] = Field(
        default=[
            "new_lead",
            "contacted",
            "nurturing",
            "qualified",
            "proposal_sent",
            "negotiation",
            "won",
            "lost",
        ],
        description="Ordered list of Kanban stage identifiers for this tenant's pipeline",
    )
    active_tools: list[str] | None = Field(
        default=None,
        description="Explicit list of tool names to enable. None = all default tools active.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary extra config keys forwarded to the runtime as-is",
    )
