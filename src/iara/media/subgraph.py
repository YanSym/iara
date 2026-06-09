"""MediaUnderstanding subgraph.

Processes attachments (audio, images, documents) before the main agent node.
Raw bytes, base64, temporary URLs, and audio files NEVER enter the LangGraph
state, prompt, log, or evidence.

Supported media types and fallback behavior:
- Audio: OpenAI Whisper transcription → text transcript
- Images: GPT-4o vision → description + extracted text
- PDF: pypdf text extraction (fallback: GPT-4o vision)
- Other documents: plain text extraction attempt
- Unknown/unsupported: explicit ``unsupported`` status

Fallback statuses:
- ``partial``: Some content extracted but incomplete
- ``unsupported``: Media type not supported by current config
- ``failed``: Processing failed with an error
"""

from __future__ import annotations

import base64
import io
from typing import Any

import httpx

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

# File extension map for Whisper (requires filename with extension)
_AUDIO_EXT_MAP: dict[str, str] = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "audio/x-m4a": "m4a",
}


async def _download_bytes(url: str, max_bytes: int) -> bytes:
    """Download file bytes from a URL with a size guard.

    Args:
        url: The download URL.
        max_bytes: Maximum allowed file size.

    Returns:
        bytes: Downloaded file content.

    Raises:
        ValueError: If the file exceeds max_bytes.
        httpx.HTTPError: On HTTP errors.
    """
    async with (
        httpx.AsyncClient(timeout=60, follow_redirects=True) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"File exceeds size limit ({max_bytes // (1024 * 1024)} MB)")
            chunks.append(chunk)
        return b"".join(chunks)


class MediaUnderstandingSubgraph:
    """Processes media attachments and returns sanitized MediaContext objects.

    Raw media bytes are processed transiently and NEVER stored in state,
    logs, or evidence. Only the extracted text/description is kept.

    Args:
        openai_api_key: OpenAI API key for Whisper and GPT-4o vision.
        audio_transcription_enabled: Whether to transcribe audio.
        vision_enabled: Whether to perform visual description of images.
        document_extraction_enabled: Whether to extract text from documents.
        max_media_size_mb: Maximum media size in MB.
    """

    def __init__(
        self,
        openai_api_key: str | None = None,
        audio_transcription_enabled: bool = True,
        vision_enabled: bool = True,
        document_extraction_enabled: bool = True,
        max_media_size_mb: int = 50,
    ) -> None:
        self._openai_key = openai_api_key
        self._audio_enabled = audio_transcription_enabled
        self._vision_enabled = vision_enabled
        self._doc_enabled = document_extraction_enabled
        self._max_bytes = max_media_size_mb * 1024 * 1024

    # ── Public API ────────────────────────────────────────────────────────────

    async def process(
        self,
        attachments: list[CanonicalAttachment],
    ) -> list[MediaContext]:
        """Process a list of CanonicalAttachment objects.

        Args:
            attachments: Attachments with _raw_url set by the normalizer.

        Returns:
            list[MediaContext]: Processing results for each attachment.
        """
        results = []
        for attachment in attachments:
            context = await self._process_attachment(attachment)
            results.append(context)
        return results

    async def process_from_dicts(
        self,
        attachment_dicts: list[dict[str, Any]],
    ) -> list[MediaContext]:
        """Process attachments from raw dicts (used when coming from job metadata).

        Each dict should have: url, content_type, type (audio/image/file), ref.

        Args:
            attachment_dicts: List of attachment metadata dicts.

        Returns:
            list[MediaContext]: Processing results for each attachment.
        """
        results = []
        for att in attachment_dicts:
            ref = att.get("ref", "unknown")
            url = att.get("url")
            content_type = att.get("content_type") or "application/octet-stream"
            att_type = att.get("type", "file")

            if not url:
                results.append(
                    MediaContext(
                        attachment_ref=ref,
                        media_type=content_type,
                        status=MediaStatus.FAILED,
                        fallback_reason="No download URL available",
                    )
                )
                continue

            ctx = await self._process_by_type(
                ref=ref,
                url=url,
                content_type=content_type,
                att_type=att_type,
            )
            results.append(ctx)
        return results

    # ── Internal routing ──────────────────────────────────────────────────────

    async def _process_attachment(self, attachment: CanonicalAttachment) -> MediaContext:
        """Process a single CanonicalAttachment.

        Args:
            attachment: The attachment to process.

        Returns:
            MediaContext: Processing result.
        """
        media_type = attachment.content_type or "application/octet-stream"

        if attachment.file_size_bytes and attachment.file_size_bytes > self._max_bytes:
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=media_type,
                status=MediaStatus.FAILED,
                fallback_reason=f"Media exceeds size limit ({self._max_bytes // (1024 * 1024)} MB)",
            )

        # Try to get the download URL from the private attribute
        raw_url: str | None = getattr(attachment, "_raw_url", None)
        if not raw_url:
            # No URL available — return partial with whatever processing_result exists
            if attachment.is_processed and attachment.processing_result:
                return MediaContext(
                    attachment_ref=attachment.attachment_ref,
                    media_type=media_type,
                    status=MediaStatus.COMPLETE,
                    extracted_text=attachment.processing_result,
                )
            return MediaContext(
                attachment_ref=attachment.attachment_ref,
                media_type=media_type,
                status=MediaStatus.PARTIAL,
                fallback_reason="No download URL available for media processing",
            )

        att_type = attachment.attachment_type.value if attachment.attachment_type else "file"
        return await self._process_by_type(
            ref=attachment.attachment_ref,
            url=raw_url,
            content_type=media_type,
            att_type=att_type,
        )

    async def _process_by_type(
        self,
        ref: str,
        url: str,
        content_type: str,
        att_type: str,
    ) -> MediaContext:
        """Route processing by attachment type.

        Args:
            ref: Opaque attachment reference.
            url: Download URL.
            content_type: MIME type.
            att_type: Attachment type string (audio/image/file).

        Returns:
            MediaContext: Processing result.
        """
        try:
            if att_type == AttachmentType.AUDIO or content_type in SUPPORTED_AUDIO_MIME_TYPES:
                return await self._process_audio(ref, url, content_type)
            elif att_type == AttachmentType.IMAGE or content_type in SUPPORTED_IMAGE_MIME_TYPES:
                return await self._process_image(ref, url, content_type)
            elif att_type == AttachmentType.FILE or content_type in SUPPORTED_TEXT_MIME_TYPES:
                return await self._process_document(ref, url, content_type)
            else:
                return MediaContext(
                    attachment_ref=ref,
                    media_type=content_type,
                    status=MediaStatus.UNSUPPORTED,
                    fallback_reason=f"Media type '{content_type}' is not supported",
                )
        except Exception as exc:
            logger.warning(
                "media_processing_failed",
                attachment_ref=ref[:8],
                content_type=content_type,
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.FAILED,
                fallback_reason=f"Processing failed: {type(exc).__name__}: {str(exc)[:100]}",
            )

    # ── Audio processing (OpenAI Whisper) ─────────────────────────────────────

    async def _process_audio(self, ref: str, url: str, content_type: str) -> MediaContext:
        """Transcribe audio using OpenAI Whisper.

        Args:
            ref: Attachment reference.
            url: Download URL.
            content_type: Audio MIME type.

        Returns:
            MediaContext: Transcription result.
        """
        if not self._audio_enabled:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Audio transcription is disabled",
            )

        if not self._openai_key:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.PARTIAL,
                fallback_reason="OpenAI API key not configured for audio transcription",
            )

        logger.info("media_audio_downloading", attachment_ref=ref[:8])
        audio_bytes = await _download_bytes(url, self._max_bytes)

        ext = _AUDIO_EXT_MAP.get(content_type, "ogg")
        filename = f"audio.{ext}"

        try:
            import openai

            client = openai.AsyncOpenAI(api_key=self._openai_key)
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, audio_bytes, content_type),
                language="pt",
            )
            logger.info("media_audio_transcribed", attachment_ref=ref[:8])
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.COMPLETE,
                extracted_text=transcript.text,
            )
        except Exception as exc:
            logger.warning(
                "media_audio_transcription_failed",
                attachment_ref=ref[:8],
                error_code=type(exc).__name__,
            )
            raise

    # ── Image processing (GPT-4o vision) ──────────────────────────────────────

    async def _process_image(self, ref: str, url: str, content_type: str) -> MediaContext:
        """Describe an image using GPT-4o vision.

        Args:
            ref: Attachment reference.
            url: Download URL (used directly as image_url for OpenAI).
            content_type: Image MIME type.

        Returns:
            MediaContext: Visual description result.
        """
        if not self._vision_enabled:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Visual description is disabled in current config",
            )

        if not self._openai_key:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.PARTIAL,
                fallback_reason="OpenAI API key not configured for vision",
            )

        logger.info("media_image_processing", attachment_ref=ref[:8])

        try:
            import openai

            client = openai.AsyncOpenAI(api_key=self._openai_key)
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Descreva esta imagem de forma detalhada. "
                                    "Extraia todo texto visível na imagem. "
                                    "Seja objetivo e factual. Responda em português."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": url, "detail": "high"},
                            },
                        ],
                    }
                ],
                max_tokens=1000,
            )
            description = response.choices[0].message.content or ""
            logger.info("media_image_described", attachment_ref=ref[:8])
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.COMPLETE,
                description=description,
                extracted_text=description,
            )
        except Exception as exc:
            logger.warning(
                "media_image_description_failed",
                attachment_ref=ref[:8],
                error_code=type(exc).__name__,
            )
            raise

    # ── Document processing (pypdf + LLM fallback) ────────────────────────────

    async def _process_document(self, ref: str, url: str, content_type: str) -> MediaContext:
        """Extract text from a document.

        For PDFs: uses pypdf for text extraction (fast, no API call).
        For scanned PDFs or images-in-PDF: falls back to GPT-4o vision.
        For other text types: decodes bytes directly.

        Args:
            ref: Attachment reference.
            url: Download URL.
            content_type: Document MIME type.

        Returns:
            MediaContext: Extracted text result.
        """
        if not self._doc_enabled:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason="Document extraction is disabled",
            )

        if content_type not in SUPPORTED_TEXT_MIME_TYPES:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.UNSUPPORTED,
                fallback_reason=f"Document type '{content_type}' is not supported",
            )

        logger.info("media_document_downloading", attachment_ref=ref[:8])
        doc_bytes = await _download_bytes(url, self._max_bytes)

        # Plain text types
        if content_type in ("text/plain", "text/csv"):
            try:
                text = doc_bytes.decode("utf-8", errors="replace")
                return MediaContext(
                    attachment_ref=ref,
                    media_type=content_type,
                    status=MediaStatus.COMPLETE,
                    extracted_text=text[:8000],
                )
            except Exception as exc:
                logger.warning("media_text_decode_failed", error_code=type(exc).__name__)
                raise

        # PDF extraction
        if content_type == "application/pdf":
            return await self._extract_pdf(ref, url, content_type, doc_bytes)

        # Other office formats — partial support
        return MediaContext(
            attachment_ref=ref,
            media_type=content_type,
            status=MediaStatus.PARTIAL,
            extracted_text=(
                f"[Documento {content_type} — extração completa não disponível nesta versão]"
            ),
            fallback_reason=f"Full extraction not implemented for {content_type}",
        )

    async def _extract_pdf(
        self, ref: str, url: str, content_type: str, pdf_bytes: bytes
    ) -> MediaContext:
        """Extract text from a PDF using pypdf.

        Falls back to GPT-4o vision if pypdf extracts no text (scanned PDF).

        Args:
            ref: Attachment reference.
            url: Original download URL (for vision fallback).
            content_type: MIME type.
            pdf_bytes: Downloaded PDF bytes.

        Returns:
            MediaContext: Extracted text result.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages_text: list[str] = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    pages_text.append(page_text)

            extracted = "\n\n".join(pages_text).strip()

            if extracted:
                logger.info(
                    "media_pdf_extracted",
                    attachment_ref=ref[:8],
                    char_count=len(extracted),
                )
                return MediaContext(
                    attachment_ref=ref,
                    media_type=content_type,
                    status=MediaStatus.COMPLETE,
                    extracted_text=extracted[:8000],
                )

            # No text extracted — likely a scanned PDF; try vision fallback
            logger.info("media_pdf_scanned_fallback", attachment_ref=ref[:8])
            return await self._pdf_vision_fallback(ref, url, content_type, pdf_bytes)

        except ImportError:
            logger.warning("media_pdf_pypdf_not_installed")
            return await self._pdf_vision_fallback(ref, url, content_type, pdf_bytes)

        except Exception as exc:
            logger.warning(
                "media_pdf_extraction_failed",
                attachment_ref=ref[:8],
                error_code=type(exc).__name__,
            )
            return await self._pdf_vision_fallback(ref, url, content_type, pdf_bytes)

    async def _pdf_vision_fallback(
        self, ref: str, url: str, content_type: str, pdf_bytes: bytes
    ) -> MediaContext:
        """Use GPT-4o to extract content from a scanned/image PDF.

        Args:
            ref: Attachment reference.
            url: Original download URL.
            content_type: MIME type.
            pdf_bytes: Downloaded PDF bytes.

        Returns:
            MediaContext: Extraction result.
        """
        if not self._openai_key:
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.PARTIAL,
                fallback_reason="PDF appears to be scanned; OpenAI key required for extraction",
            )

        try:
            import openai

            pdf_b64 = base64.b64encode(pdf_bytes).decode()
            client = openai.AsyncOpenAI(api_key=self._openai_key)

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Extraia e resuma todo o conteúdo textual deste documento PDF. "
                                    "Seja abrangente e preciso. Responda em português."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:application/pdf;base64,{pdf_b64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=2000,
            )
            text = response.choices[0].message.content or ""
            logger.info("media_pdf_vision_extracted", attachment_ref=ref[:8])
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.COMPLETE,
                extracted_text=text,
            )
        except Exception as exc:
            logger.warning(
                "media_pdf_vision_failed",
                attachment_ref=ref[:8],
                error_code=type(exc).__name__,
            )
            return MediaContext(
                attachment_ref=ref,
                media_type=content_type,
                status=MediaStatus.PARTIAL,
                fallback_reason=f"PDF vision extraction failed: {type(exc).__name__}",
            )
