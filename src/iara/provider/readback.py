"""Readback service — confirms provider side effects were actually applied.

After executing a provider command, the readback service re-reads the relevant
provider state to verify the mutation was applied. This is required for all
write operations per INV-04.
"""

from __future__ import annotations

import asyncio
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_READBACK_RETRIES = 3
DEFAULT_READBACK_DELAY_SECONDS = 2


class ReadbackService:
    """Verifies provider mutations via read-back confirmation.

    Args:
        adapter: The ProviderAdapter to use for reads.
        max_retries: Maximum number of readback attempts.
        delay_seconds: Delay between attempts.
    """

    def __init__(
        self,
        adapter: Any,  # ProviderAdapter
        max_retries: int = DEFAULT_READBACK_RETRIES,
        delay_seconds: float = DEFAULT_READBACK_DELAY_SECONDS,
    ) -> None:
        self._adapter = adapter
        self._max_retries = max_retries
        self._delay_seconds = delay_seconds

    async def confirm_message_sent(
        self,
        command_id: str,
        conversation_id: str,
        expected_message_ref: str,
        security_context: Any,
    ) -> bool:
        """Confirm that a message was sent to a conversation.

        Polls the conversation messages until the expected message ref is found
        or the retry limit is exceeded.

        Args:
            command_id: The command that sent the message.
            conversation_id: The conversation to check.
            expected_message_ref: Opaque ref of the expected message.
            security_context: Verified security context.

        Returns:
            bool: True if confirmed, False if not confirmed within retries.

        Raises:
            ReadbackFailedError: If readback cannot be completed due to an error.
        """
        for attempt in range(self._max_retries):
            try:
                context = await self._adapter.read_conversation_context(
                    tenant_id=str(security_context.tenant_id),
                    conversation_id=conversation_id,
                    security_context=security_context,
                )
                message_refs = context.get("message_refs", [])
                if expected_message_ref in message_refs:
                    logger.info(
                        "readback_confirmed",
                        command_id=command_id,
                        conversation_id=conversation_id,
                        attempt=attempt + 1,
                    )
                    return True
            except Exception as exc:
                logger.warning(
                    "readback_attempt_failed",
                    command_id=command_id,
                    attempt=attempt + 1,
                    error_code=type(exc).__name__,
                )

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._delay_seconds * (attempt + 1))

        logger.error(
            "readback_exhausted",
            command_id=command_id,
            conversation_id=conversation_id,
            max_retries=self._max_retries,
        )
        return False

    async def confirm_label_applied(
        self,
        command_id: str,
        conversation_id: str,
        expected_label_ref: str,
        security_context: Any,
    ) -> bool:
        """Confirm that a label was applied to a conversation.

        Args:
            command_id: The command that applied the label.
            conversation_id: The conversation to check.
            expected_label_ref: Opaque ref of the expected label.
            security_context: Verified security context.

        Returns:
            bool: True if confirmed.
        """
        for attempt in range(self._max_retries):
            try:
                context = await self._adapter.read_conversation_context(
                    tenant_id=str(security_context.tenant_id),
                    conversation_id=conversation_id,
                    security_context=security_context,
                )
                label_refs = context.get("label_refs", [])
                if expected_label_ref in label_refs:
                    return True
            except Exception:
                pass

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._delay_seconds)

        return False
