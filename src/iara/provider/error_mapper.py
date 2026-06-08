"""Provider error mapper — sanitizes raw provider errors into typed IaraErrors.

Raw provider error messages may contain PII, tokens, or internal system details.
The ProviderErrorMapper transforms them into sanitized, typed errors suitable
for logging and audit events.
"""

from __future__ import annotations

from iara.contracts.errors import ProviderError
from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Mapping from HTTP status codes to sanitized error codes
HTTP_STATUS_ERROR_MAP: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "AUTHENTICATION_FAILED",
    403: "AUTHORIZATION_FAILED",
    404: "RESOURCE_NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "PROVIDER_INTERNAL_ERROR",
    502: "PROVIDER_UNAVAILABLE",
    503: "PROVIDER_UNAVAILABLE",
    504: "PROVIDER_TIMEOUT",
}


class ProviderErrorMapper:
    """Maps raw provider errors to sanitized, typed IaraErrors.

    Args:
        provider_name: The provider name (e.g. ``chatwoot``).
    """

    def __init__(self, provider_name: str) -> None:
        self._provider = provider_name

    def map_http_error(self, status_code: int, raw_message: str | None = None) -> ProviderError:
        """Map an HTTP error response to a sanitized ProviderError.

        The raw message is NEVER included in the returned error — only a
        sanitized code and generic summary.

        Args:
            status_code: HTTP response status code.
            raw_message: Raw error message from the provider (NOT propagated).

        Returns:
            ProviderError: A sanitized, typed provider error.
        """
        error_code = HTTP_STATUS_ERROR_MAP.get(status_code, "PROVIDER_ERROR")
        sanitized_summary = self._sanitize_summary(status_code)

        logger.warning(
            "provider_error_mapped",
            provider=self._provider,
            status_code=status_code,
            error_code=error_code,
            # raw_message intentionally excluded from log
        )

        return ProviderError(
            message=sanitized_summary,
            provider=self._provider,
            status_code=status_code,
        )

    def map_exception(self, exc: Exception) -> ProviderError:
        """Map a raw exception to a sanitized ProviderError.

        Args:
            exc: The raw exception (message is NOT propagated).

        Returns:
            ProviderError: A sanitized, typed provider error.
        """
        error_code = type(exc).__name__
        sanitized_summary = f"Provider {self._provider!r} operation failed ({error_code})"

        logger.warning(
            "provider_exception_mapped",
            provider=self._provider,
            error_code=error_code,
            # exc details intentionally excluded from log
        )

        return ProviderError(
            message=sanitized_summary,
            provider=self._provider,
        )

    def _sanitize_summary(self, status_code: int) -> str:
        """Generate a sanitized error summary for a given HTTP status.

        Args:
            status_code: HTTP status code.

        Returns:
            str: Sanitized error summary.
        """
        summaries = {
            400: f"Provider {self._provider!r} rejected the request (invalid input)",
            401: f"Provider {self._provider!r} authentication failed",
            403: f"Provider {self._provider!r} authorization failed",
            404: f"Provider {self._provider!r} resource not found",
            409: f"Provider {self._provider!r} reported a conflict",
            422: f"Provider {self._provider!r} validation failed",
            429: f"Provider {self._provider!r} rate limit exceeded",
            500: f"Provider {self._provider!r} internal error",
            502: f"Provider {self._provider!r} unavailable (bad gateway)",
            503: f"Provider {self._provider!r} unavailable",
            504: f"Provider {self._provider!r} timeout",
        }
        return summaries.get(
            status_code, f"Provider {self._provider!r} error (status {status_code})"
        )
