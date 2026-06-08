"""MediaUnderstanding subgraph.

Processes attachments (audio, images, documents) before the main agent node.
Raw bytes, base64, temporary URLs, and audio files NEVER enter the LangGraph
state, prompt, log, or evidence.

Supported media types and fallback behavior:
- Audio: transcription → text transcript
- Images/scanned docs: OCR/visual description (if enabled)
- PDF/DOCX/PPTX/XLSX/text: text extraction (if enabled)
- Unknown/unsupported: explicit ``unsupported`` status

Fallback statuses:
- ``partial``: Some content extracted but incomplete
- ``unsupported``: Media type not supported by current config
- ``failed``: Processing failed with an error
"""

from __future__ import annotations

from iara.contracts.events import AttachmentType, CanonicalAttachment
from iara.contracts.state import MediaContext, MediaStatus
from iara.observability.logging import get_logger

logger = get_logger(__name__)

# MIME types we can extract text from
SUPPORTED_TEXT_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)

SUPPORTED_AUDIO_MIME_TYPES: frozenset[str] = frozenset(
    {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
    }
)

SUPPORTED_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)


class MediaUnderstandingSubgraph:
    """Processes media attachments and returns sanitized MediaContext objects.

    Raw media bytes are processed transiently and NEVER stored in state,
    logs, or evidence. Only the extracted text/description is kept.

    Args:
        audio_transcription_enabled: Whether to transcribe audio.
        vision_enabled: Whether to perform visual description of images.
        document_extraction_enabled: Whether to extract text from documents.
        max_media_size_mb: Maximum media size in MB.
    """

    def __init__(
        self,
        audio_transcription_enabled: bool = True,
        vision_enabled: bool = False,
        document_extraction_enabled: bool = True,
        max_media_size_mb: int = 50,
    ) -> None:
        self._audio_enabled = audio_transcription_enabled
        self._vision_enabled = vision_enabled
        self._doc_enabled = document_extraction_enabled
        self._max_bytes = max_media_size_mb * 1024 * 1024

    async def process(
        self,
        attachments: list[CanonicalAttachment],
    ) -> list[MediaContext]:
        """Process a list of attachments and return MediaContext results.

        Args:
            attachments: List of canonical attachments to process.

        Returns:
            list[MediaContext]: Processing results for each attachment.
        """
        results = []
        for attachment in attachments:
            context = await self._process_attachment(attachment)
            results.append(context)
        return results

    async def _process_attachment(self, attachment: CanonicalAttachment) -> MediaContext:
        """Process a single attachment.

        Args:
            attachment: The attachment to process.

        Returns:
            MediaContext: Processing result.
        """
        media_type = attachment.content_type or "application/octet-stream"

        # Size guard
        if attachment.file_size_bytes and attachment.file_size_bytes > self._max_bytes:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=media_type,
                status=MediaStatus.FAILED,
                fallback_reason=f"Media exceeds size limit ({self._max_bytes // (1024*1024)}MB)",
            )

        # Route by type
        if attachment.attachment_type == AttachmentType.AUDIO:
            return await self._process_audio(attachment)
        elif attachment.attachment_type == AttachmentType.IMAGE:
            return await self._process_image(attachment)
        elif attachment.attachment_type == AttachmentType.FILE:
            return await self._process_document(attachment)
        else:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=media_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason=f"Attachment type {attachment.attachment_type!r} not supported",
            )

    async def _process_audio(self, attachment: CanonicalAttachment) -> MediaContext:
        """Process an audio attachment (transcription).

        Args:
            attachment: Audio attachment.

        Returns:
            MediaContext: Transcription result or fallback.
        """
        if not self._audio_enabled:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=attachment.content_type or "audio/unknown",
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Audio transcription is disabled",
            )

        # If the attachment has already been processed (e.g. by a prior subgraph call)
        if attachment.is_processed and attachment.processing_result:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=attachment.content_type or "audio/unknown",
                status=MediaStatus.COMPLETE,
                extracted_text=attachment.processing_result,
            )

        # Stub: in real implementation, call transcription service
        # Raw bytes/URLs are NEVER stored in the returned MediaContext
        logger.info(
            "media_audio_processing",
            attachment_ref=attachment.attachment_ref[:8],
            content_type=attachment.content_type,
        )

        return MediaContext(
            attachment_ref=attachment.attachment_ref,
            media_type=attachment.content_type or "audio/ogg",
            status=MediaStatus.PARTIAL,
            extracted_text="[Audio transcription pending — stub implementation]",
            fallback_reason="Transcription service not yet wired",
        )

    async def _process_image(self, attachment: CanonicalAttachment) -> MediaContext:
        """Process an image attachment (OCR or visual description).

        Args:
            attachment: Image attachment.

        Returns:
            MediaContext: Description result or fallback.
        """
        if not self._vision_enabled:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=attachment.content_type or "image/unknown",
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Visual description is disabled in current config",
            )

        return MediaContext(
            attachment_ref=attachment.attachment_ref,
            media_type=attachment.content_type or "image/jpeg",
            status=MediaStatus.PARTIAL,
            description="[Visual description pending — stub implementation]",
            fallback_reason="Vision service not yet wired",
        )

    async def _process_document(self, attachment: CanonicalAttachment) -> MediaContext:
        """Process a document attachment (text extraction).

        Args:
            attachment: Document attachment.

        Returns:
            MediaContext: Extracted text result or fallback.
        """
        if not self._doc_enabled:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=attachment.content_type or "application/octet-stream",
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Document extraction is disabled",
            )

        content_type = attachment.content_type or ""
        if content_type not in SUPPORTED_TEXT_MIME_TYPES:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=content_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason=f"Document type {content_type!r} is not supported",
            )

        return MediaContext(
            attachment_ref=attachment.attachment_ref,
            media_type=content_type,
            status=MediaStatus.PARTIAL,
            extracted_text="[Document text extraction pending — stub implementation]",
            fallback_reason="Document extraction service not yet wired",
        )
