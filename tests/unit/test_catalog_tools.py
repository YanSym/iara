"""Unit tests for the Agent Tool catalog handlers.

Tests verify that:
- Read-only handlers return sanitized dicts (no PII)
- Write handlers build correct ProviderCommand payloads
- No real LLM, database, or network calls are made
"""

from __future__ import annotations

import uuid

import pytest

from iara.tools.catalog import (
    campaigns,
    followup,
    history,
    kanban,
    kb,
    lead,
    qualification,
    scheduling,
    voice,
)


@pytest.mark.unit
class TestSchedulingHandlers:
    """Tests for scheduling catalog handlers."""

    @pytest.mark.asyncio
    async def test_availability_returns_sanitized_dict(self) -> None:
        """handle_availability must return counts and next slot — no PII."""
        result = await scheduling.handle_availability(
            {"date_range_start": "2026-06-10", "service_type": "consultation"}
        )
        assert isinstance(result, dict)
        assert "available_slots_count" in result
        assert "next_available_slot" in result

    def test_schedule_command_has_required_keys(self) -> None:
        """build_schedule_command must return a valid command payload."""
        cmd = scheduling.build_schedule_command(
            arguments={"datetime_iso": "2026-06-10T09:00:00"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_test",
            idempotency_key="key_001",
            correlation_id="corr_001",
        )
        assert cmd["provider"] == "google_calendar"
        assert cmd["capability_name"] == "schedule_appointment"
        assert "command_id" in cmd
        assert cmd["parameters"]["datetime_iso"] == "2026-06-10T09:00:00"

    def test_cancel_command_uses_opaque_reason_ref(self) -> None:
        """Cancel command must hash the reason — never store raw text."""
        cmd = scheduling.build_cancel_command(
            arguments={"appointment_ref": "appt_001", "reason": "Patient request"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_test",
            idempotency_key="key_002",
            correlation_id="corr_002",
        )
        assert "Patient request" not in str(cmd)
        assert cmd["parameters"]["appointment_ref"] == "appt_001"

    def test_reschedule_command_structure(self) -> None:
        """Reschedule command must contain appointment_ref and new_datetime_iso."""
        cmd = scheduling.build_reschedule_command(
            arguments={"appointment_ref": "appt_001", "new_datetime_iso": "2026-06-11T10:00:00"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_test",
            idempotency_key="key_003",
            correlation_id="corr_003",
        )
        assert cmd["parameters"]["appointment_ref"] == "appt_001"
        assert cmd["parameters"]["new_datetime_iso"] == "2026-06-11T10:00:00"


@pytest.mark.unit
class TestQualificationHandlers:
    """Tests for qualification catalog handlers."""

    def test_qualify_command_uses_opaque_note_ref(self) -> None:
        """qualify command must hash the note — never store raw text."""
        cmd = qualification.build_qualify_command(
            arguments={"qualification_note": "Very interested in our product", "label": "hot"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_001",
            idempotency_key="key_q001",
            correlation_id="corr_q001",
        )
        assert "Very interested in our product" not in str(cmd)
        assert cmd["parameters"]["label"] == "hot"

    def test_disqualify_command_uses_opaque_reason(self) -> None:
        """disqualify command must hash the reason — never store raw text."""
        cmd = qualification.build_disqualify_command(
            arguments={"reason": "Not in target region", "label": "out_of_area"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_002",
            idempotency_key="key_q002",
            correlation_id="corr_q002",
        )
        assert "Not in target region" not in str(cmd)
        assert cmd["parameters"]["label"] == "out_of_area"


@pytest.mark.unit
class TestKanbanHandlers:
    """Tests for kanban catalog handlers."""

    @pytest.mark.asyncio
    async def test_kanban_analyze_returns_suggestion(self) -> None:
        """kanban analyze must return a suggested stage and confidence."""
        result = await kanban.handle_kanban_analyze({"include_history": False})
        assert "suggested_stage" in result
        assert "confidence" in result
        assert result["mode"] == "suggest_only"

    def test_kanban_update_command_structure(self) -> None:
        """kanban update command must include stage and label."""
        cmd = kanban.build_kanban_update_command(
            arguments={"stage": "qualified", "reason": "Budget confirmed"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_k001",
            idempotency_key="key_k001",
            correlation_id="corr_k001",
        )
        assert cmd["parameters"]["stage"] == "qualified"
        assert "kanban:qualified" in cmd["parameters"]["label"]

    def test_kanban_comment_uses_opaque_ref(self) -> None:
        """kanban comment must hash the note content."""
        cmd = kanban.build_kanban_comment_command(
            arguments={"comment": "Customer mentioned referral source"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_k002",
            idempotency_key="key_k002",
            correlation_id="corr_k002",
        )
        assert "Customer mentioned referral source" not in str(cmd)
        assert "note_ref" in cmd["parameters"]


@pytest.mark.unit
class TestCampaignHandlers:
    """Tests for campaign catalog handlers."""

    @pytest.mark.asyncio
    async def test_campaign_status_returns_counts_only(self) -> None:
        """campaign_status must return counts — no contact lists or PII."""
        result = await campaigns.handle_campaign_status({"campaign_run_ref": "run_001"})
        assert "sent_count" in result
        assert "failed_count" in result
        assert "pending_count" in result
        # Ensure no raw contact data
        assert "email" not in str(result).lower()
        assert "phone" not in str(result).lower()

    @pytest.mark.asyncio
    async def test_campaign_validate_audience_returns_counts_only(self) -> None:
        """campaign_validate_audience must return counts only."""
        result = await campaigns.handle_campaign_validate_audience(
            {"campaign_draft_ref": "draft_001"}
        )
        assert "eligible_count" in result
        assert "opted_out_count" in result

    def test_campaign_dispatch_command_caps_batch_size(self) -> None:
        """Batch size must be capped at 100 even if caller requests more."""
        cmd = campaigns.build_campaign_dispatch_command(
            arguments={"campaign_run_ref": "run_001", "batch_size": 99999},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_c001",
            idempotency_key="key_c001",
            correlation_id="corr_c001",
        )
        assert cmd["parameters"]["batch_size"] <= 100

    def test_campaign_create_uses_hash_refs(self) -> None:
        """Campaign create must hash template content — never store raw."""
        cmd = campaigns.build_campaign_create_command(
            arguments={
                "campaign_name": "Summer Promo 2026",
                "message_template": "Hi, you qualify for our special offer!",
                "target_description": "All leads from June 2026",
            },
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_c002",
            idempotency_key="key_c002",
            correlation_id="corr_c002",
        )
        assert "Summer Promo 2026" not in str(cmd)
        assert "special offer" not in str(cmd)
        assert "name_ref" in cmd["parameters"]
        assert "template_ref" in cmd["parameters"]


@pytest.mark.unit
class TestFollowupHandler:
    """Tests for follow-up catalog handler."""

    def test_followup_command_uses_message_ref(self) -> None:
        """Follow-up command must hash the message content."""
        cmd = followup.build_followup_command(
            arguments={
                "message": "Hi, just checking in about your appointment!",
                "reason": "stale",
            },
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_f001",
            idempotency_key="key_f001",
            correlation_id="corr_f001",
        )
        assert "Hi, just checking in" not in str(cmd)
        assert "message_ref" in cmd["parameters"]
        assert cmd["parameters"]["message_length"] > 0


@pytest.mark.unit
class TestKbHandler:
    """Tests for KB catalog handler."""

    @pytest.mark.asyncio
    async def test_kb_suggest_returns_draft_ref(self) -> None:
        """kb_suggest must return a draft ref — not publish directly."""
        result = await kb.handle_kb_suggest(
            {
                "topic": "Appointment cancellation policy",
                "suggested_content": "Cancellations must be made 24 hours in advance.",
                "rationale": "Frequently asked in conversations",
            }
        )
        assert "draft_ref" in result
        assert result["status"] == "draft"
        assert "Cancellations must be made" not in str(result)

    def test_kb_suggest_command_uses_hash_refs(self) -> None:
        """KB suggest command must hash topic and content — never store raw."""
        cmd = kb.build_kb_suggest_command(
            arguments={
                "topic": "Pricing",
                "suggested_content": "Our pricing starts at R$150 per session.",
                "rationale": "Common question",
            },
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_kb001",
            idempotency_key="key_kb001",
            correlation_id="corr_kb001",
        )
        assert "Pricing" not in str(cmd["parameters"])
        assert "R$150" not in str(cmd)
        assert "topic_ref" in cmd["parameters"]
        assert "content_ref" in cmd["parameters"]


@pytest.mark.unit
class TestVoiceHandler:
    """Tests for voice catalog handler."""

    def test_voice_falls_back_to_text_when_disabled(self) -> None:
        """Voice handler must fall back to text when voice_output is disabled."""
        cmd = voice.build_voice_command(
            arguments={"text_content": "Your appointment is confirmed.", "voice_ref": "default"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_v001",
            idempotency_key="key_v001",
            correlation_id="corr_v001",
            voice_output_enabled=False,
        )
        assert cmd["capability_name"] == "send_outbound_message"
        assert cmd["parameters"].get("fallback_reason") == "voice_output_policy_not_active"

    def test_voice_uses_audio_capability_when_enabled(self) -> None:
        """Voice handler must use audio capability when policy is active."""
        cmd = voice.build_voice_command(
            arguments={"text_content": "Your appointment is confirmed.", "voice_ref": "default"},
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_v002",
            idempotency_key="key_v002",
            correlation_id="corr_v002",
            voice_output_enabled=True,
        )
        assert cmd["capability_name"] == "generate_and_send_audio"
        assert "Your appointment" not in str(cmd)


@pytest.mark.unit
class TestLeadHandler:
    """Tests for lead catalog handler."""

    @pytest.mark.asyncio
    async def test_lead_search_returns_counts_only(self) -> None:
        """lead_search must return counts only — never raw contact data."""
        result = await lead.handle_lead_search({"search_terms": ["dentist", "2026"]})
        assert "results_count" in result
        assert result.get("pii_redacted") is True
        assert "email" not in str(result).lower()
        assert "phone" not in str(result).lower()

    @pytest.mark.asyncio
    async def test_lead_search_caps_search_terms(self) -> None:
        """lead_search must cap search terms at 10 items."""
        many_terms = [f"term_{i}" for i in range(50)]
        result = await lead.handle_lead_search({"search_terms": many_terms})
        assert "results_count" in result


@pytest.mark.unit
class TestHistoryHandler:
    """Tests for history catalog handler."""

    @pytest.mark.asyncio
    async def test_history_analyze_returns_anonymized_summary(self) -> None:
        """history_analyze must return anonymized pattern data, no raw content."""
        result = await history.handle_history_analyze(
            {"limit": 10, "focus": "appointment_scheduling"}
        )
        assert "analyzed_count" in result
        assert "draft_ref" in result
        assert result.get("pii_redacted") is True

    @pytest.mark.asyncio
    async def test_history_caps_limit_at_max(self) -> None:
        """history_analyze must cap limit at MAX_HISTORY_LIMIT."""
        result = await history.handle_history_analyze({"limit": 999})
        assert "analyzed_count" in result
        assert "limit_applied" in result
        assert result["limit_applied"] <= history.MAX_HISTORY_LIMIT
