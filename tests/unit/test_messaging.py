"""Unit tests for the messaging layer (publisher/consumer/topology).

All tests use mocks — no real RabbitMQ connection is made.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from iara.messaging.publisher import ConversationJob, MessagePublisher
from iara.messaging.topology import (
    EXCHANGE_JOBS,
    QUEUE_CONVERSATION_JOBS,
    ROUTING_CONVERSATION_JOB,
)


@pytest.mark.unit
class TestConversationJob:
    """Tests for ConversationJob serialization."""

    def test_to_dict_includes_all_fields(self) -> None:
        """ConversationJob.to_dict must include all required keys."""
        job = ConversationJob(
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_001",
            correlation_id="corr_001",
            idempotency_key="key_001",
            event_ref="sha256:abc123",
        )
        d = job.to_dict()
        assert "tenant_id" in d
        assert "conversation_id" in d
        assert "correlation_id" in d
        assert "idempotency_key" in d
        assert "event_ref" in d
        assert "scheduled_at" in d

    def test_to_dict_is_json_serializable(self) -> None:
        """ConversationJob.to_dict must produce JSON-serializable output."""
        job = ConversationJob(
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_002",
            correlation_id="corr_002",
            idempotency_key="key_002",
            event_ref="sha256:def456",
        )
        serialized = json.dumps(job.to_dict())
        assert isinstance(serialized, str)
        assert "conv_002" in serialized


@pytest.mark.unit
class TestTopologyConstants:
    """Tests for topology constant names (used for RabbitMQ exchange/queue declaration)."""

    def test_exchange_jobs_name(self) -> None:
        """Jobs exchange must follow iara.* naming convention."""
        assert EXCHANGE_JOBS.startswith("iara.")

    def test_queue_conversation_jobs_name(self) -> None:
        """Conversation jobs queue must follow iara.* naming convention."""
        assert QUEUE_CONVERSATION_JOBS.startswith("iara.")

    def test_routing_key_convention(self) -> None:
        """Routing key must follow job.* convention."""
        assert ROUTING_CONVERSATION_JOB.startswith("job.")


@pytest.mark.unit
class TestMessagePublisher:
    """Tests for MessagePublisher using a mocked aio_pika channel."""

    @pytest.mark.asyncio
    async def test_publish_conversation_job_calls_exchange(self) -> None:
        """publish_conversation_job must call exchange.publish exactly once."""
        mock_exchange = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.get_exchange = AsyncMock(return_value=mock_exchange)

        publisher = MessagePublisher(channel=mock_channel)
        job = ConversationJob(
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_pub_001",
            correlation_id="corr_pub_001",
            idempotency_key="key_pub_001",
            event_ref="sha256:ghi789",
        )

        await publisher.publish_conversation_job(job)

        mock_channel.get_exchange.assert_called_once_with(EXCHANGE_JOBS)
        mock_exchange.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_published_message_is_persistent(self) -> None:
        """Published messages must use PERSISTENT delivery mode."""
        import aio_pika

        mock_exchange = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.get_exchange = AsyncMock(return_value=mock_exchange)

        publisher = MessagePublisher(channel=mock_channel)
        job = ConversationJob(
            tenant_id=str(uuid.uuid4()),
            conversation_id="conv_pub_002",
            correlation_id="corr_pub_002",
            idempotency_key="key_pub_002",
            event_ref="sha256:jkl012",
        )

        await publisher.publish_conversation_job(job)

        call_args = mock_exchange.publish.call_args
        message = call_args[0][0]
        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
