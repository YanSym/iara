"""Eligibility checker — decides whether a normalized event should be processed.

The eligibility check is the first gate after normalization. Events that are
outgoing, sent by bots/system, are private notes, are duplicates, or are
in the debounce window are rejected before queuing any work.
"""

from __future__ import annotations

from iara.contracts.events import (
    EligibilityDecision,
    EligibilityReason,
    MessageType,
    NormalizedChatwootEvent,
    SenderType,
)
from iara.contracts.tenancy import TenantContext
from iara.eligibility.normalizer import _make_account_ref
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class EligibilityChecker:
    """Determines whether a normalized event is eligible for agent processing.

    Eligibility rules (in order of evaluation):
    1. Account ID must match the tenant's provider_account_id binding.
    2. Event must be a message event (not just a status change for now).
    3. Message direction must be INCOMING (not OUTGOING, ACTIVITY, TEMPLATE).
    4. Sender must not be a bot or system.
    5. Message must not be a private note.
    6. Event must not be a duplicate (checked against idempotency ledger).
    7. Conversation must not be in the debounce window.

    Args:
        idempotency_checker: Async callable that checks if a key has been seen.
        debounce_checker: Async callable that checks if a conversation is debouncing.
        tenant_context: The verified tenant context.
    """

    def __init__(
        self,
        tenant_context: TenantContext,
        idempotency_checker: IdempotencyChecker | None = None,
        debounce_checker: DebounceChecker | None = None,
    ) -> None:
        self._tenant = tenant_context
        self._idempotency_checker = idempotency_checker
        self._debounce_checker = debounce_checker

    async def check(self, event: NormalizedChatwootEvent) -> EligibilityDecision:
        """Evaluate eligibility for the given normalized event.

        Args:
            event: The normalized Chatwoot event to evaluate.

        Returns:
            EligibilityDecision: Accept, reject, or enrichment-needed decision.
        """
        # 1. Cross-tenant / account binding check (fail-closed).
        # Hash the tenant's bound account ID and compare to the opaque ref in the event.
        # Both are derived via _make_account_ref so the comparison is hash-to-hash.
        expected_account_ref = _make_account_ref(self._tenant.provider_account_id)
        if event.account_id_ref != expected_account_ref:
            logger.warning(
                "eligibility_account_mismatch",
                correlation_id=event.correlation_id,
                tenant_ref=self._tenant.tenant_key[:8],
                expected_ref=expected_account_ref,
                received_ref=event.account_id_ref,
            )
            return EligibilityDecision.reject(
                EligibilityReason.ACCOUNT_MISMATCH,
                "account binding mismatch",
            )

        # 2. Non-message events — must carry a message_type to be processable.
        # Events like conversation_created or conversation_status_changed arrive
        # with message_type=None; the agent graph expects a real user message.
        if event.message_type is None:
            return EligibilityDecision.reject(
                EligibilityReason.UNSUPPORTED_EVENT_TYPE,
                "event has no message_type — not a processable message event",
            )

        # 3. Outgoing message check
        if event.message_type == MessageType.OUTGOING:
            return EligibilityDecision.reject(
                EligibilityReason.OUTGOING_MESSAGE,
                "outgoing messages do not trigger agent",
            )

        # 4. Activity / template message
        if event.message_type in (MessageType.ACTIVITY, MessageType.TEMPLATE):
            return EligibilityDecision.reject(
                EligibilityReason.UNSUPPORTED_EVENT_TYPE,
                f"message_type={event.message_type} is not eligible",
            )

        # 5. Bot / system sender
        if event.sender_type in (SenderType.AGENT_BOT, SenderType.SYSTEM):
            return EligibilityDecision.reject(
                EligibilityReason.BOT_SENDER,
                f"sender_type={event.sender_type} is not eligible",
            )

        # 6. Private note — never processed by agent
        if event.is_private:
            return EligibilityDecision.reject(
                EligibilityReason.PRIVATE_NOTE,
                "private notes are excluded from agent processing",
            )

        # 7. Idempotency / duplicate check
        if self._idempotency_checker is not None:
            is_duplicate = await self._idempotency_checker.is_duplicate(event.idempotency_key)
            if is_duplicate:
                return EligibilityDecision.reject(
                    EligibilityReason.DUPLICATE_EVENT,
                    "idempotency key already seen",
                )

        # 8. Debounce check
        if self._debounce_checker is not None:
            is_debouncing = await self._debounce_checker.is_debouncing(
                tenant_id=str(self._tenant.tenant_id),
                conversation_id=event.conversation_id,
            )
            if is_debouncing:
                return EligibilityDecision.reject(
                    EligibilityReason.DEBOUNCE_ACTIVE,
                    "conversation is in debounce window — event queued",
                )

        logger.info(
            "eligibility_accepted",
            correlation_id=event.correlation_id,
            event_type=event.event_type,
            message_type=str(event.message_type),
        )
        return EligibilityDecision.accept()


class IdempotencyChecker:
    """Protocol for checking if an idempotency key has already been processed."""

    async def is_duplicate(self, idempotency_key: str) -> bool:
        """Return True if the key has already been processed.

        Args:
            idempotency_key: The key to check.

        Returns:
            bool: True if this is a duplicate.
        """
        return False


class DebounceChecker:
    """Protocol for checking if a conversation is in the debounce window."""

    async def is_debouncing(self, tenant_id: str, conversation_id: str) -> bool:
        """Return True if the conversation is currently debouncing.

        Args:
            tenant_id: The tenant UUID string.
            conversation_id: The conversation identifier.

        Returns:
            bool: True if the conversation is in the debounce window.
        """
        return False
