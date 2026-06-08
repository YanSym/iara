"""Campaign tool handlers — create, validate, approve, dispatch, status, cancel.

Campaign dispatch is gated by CampaignMode policy (draft_only by default per INV-06).
The dispatch path requires explicit human approval before any messages are sent.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def handle_campaign_status(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the status of a campaign run (read-only).

    Args:
        arguments: Tool arguments (campaign_run_ref).

    Returns:
        dict[str, Any]: Campaign status summary (counts, status — no contact lists).
    """
    campaign_run_ref = arguments.get("campaign_run_ref", "")

    logger.info(
        "tool_campaign_status",
        campaign_run_ref=campaign_run_ref,
    )

    # In production: query campaign runs table. Return counts only, never contact lists.
    return {
        "campaign_run_ref": campaign_run_ref,
        "status": "draft",
        "sent_count": 0,
        "failed_count": 0,
        "pending_count": 0,
        "note": "Connect campaign management service for real data.",
    }


async def handle_campaign_validate_audience(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate campaign audience (read-only — counts only, no PII).

    Args:
        arguments: Tool arguments (campaign_draft_ref).

    Returns:
        dict[str, Any]: Audience counts — no individual contact data.
    """
    campaign_draft_ref = arguments.get("campaign_draft_ref", "")

    logger.info(
        "tool_campaign_validate_audience",
        campaign_draft_ref=campaign_draft_ref,
    )

    # Return counts only — never contact lists or PII (INV-05)
    return {
        "campaign_draft_ref": campaign_draft_ref,
        "eligible_count": 0,
        "opted_out_count": 0,
        "invalid_count": 0,
        "note": "Audience validation — stub implementation.",
    }


def build_campaign_create_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to create a campaign draft.

    Args:
        arguments: Tool arguments (campaign_name, message_template, target_description).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    campaign_name = arguments.get("campaign_name", "")
    message_template = arguments.get("message_template", "")
    target_description = arguments.get("target_description", "")

    # Never store raw template or name in command — use hashed refs (INV-05)
    name_ref = "campaign:" + hashlib.sha256(campaign_name.encode()).hexdigest()[:12]
    template_ref = "template:" + hashlib.sha256(message_template.encode()).hexdigest()[:12]

    logger.info(
        "tool_campaign_create_draft",
        name_ref=name_ref,
        template_ref=template_ref,
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "create_campaign_draft",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "name_ref": name_ref,
            "template_ref": template_ref,
            "target_description_ref": hashlib.sha256(target_description.encode()).hexdigest()[:12],
        },
    }


def build_campaign_approval_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to request human approval for a campaign.

    Args:
        arguments: Tool arguments (campaign_draft_ref, approver_ref).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    campaign_draft_ref = arguments.get("campaign_draft_ref", "")
    approver_ref = arguments.get("approver_ref", "")

    logger.info(
        "tool_campaign_request_approval",
        campaign_draft_ref=campaign_draft_ref,
        has_approver=bool(approver_ref),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "request_campaign_approval",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "campaign_draft_ref": campaign_draft_ref,
            "approver_ref": approver_ref,
        },
    }


def build_campaign_dispatch_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to dispatch an approved campaign batch.

    Requires explicit CampaignMode.APPROVED_SEND policy — blocked in draft_only.

    Args:
        arguments: Tool arguments (campaign_run_ref, batch_size).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    campaign_run_ref = arguments.get("campaign_run_ref", "")
    batch_size = min(int(arguments.get("batch_size", 10)), 100)  # Hard cap at 100

    logger.info(
        "tool_campaign_dispatch",
        campaign_run_ref=campaign_run_ref,
        batch_size=batch_size,
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "dispatch_campaign_batch",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "campaign_run_ref": campaign_run_ref,
            "batch_size": batch_size,
        },
    }


def build_campaign_cancel_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to cancel pending campaign messages.

    Args:
        arguments: Tool arguments (campaign_run_ref, reason).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    campaign_run_ref = arguments.get("campaign_run_ref", "")
    reason = arguments.get("reason", "")

    logger.info(
        "tool_campaign_cancel",
        campaign_run_ref=campaign_run_ref,
        has_reason=bool(reason),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "cancel_campaign_pending",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "campaign_run_ref": campaign_run_ref,
            "reason_ref": hashlib.sha256(reason.encode()).hexdigest()[:16] if reason else "",
        },
    }
