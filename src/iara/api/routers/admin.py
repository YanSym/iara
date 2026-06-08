"""Admin router — health check and sandbox endpoints.

These endpoints are for operational monitoring and sandbox testing only.
They never expose real tenant data or production paths.
"""

from __future__ import annotations

from fastapi import APIRouter, status

router = APIRouter(tags=["admin"])


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Detailed health check",
)
async def detailed_health() -> dict[str, str]:
    """Return detailed service health status.

    Returns:
        dict[str, str]: Health status with component statuses.
    """
    return {
        "status": "ok",
        "service": "iara-runtime",
        "version": "0.1.0",
        "environment": "development",
    }


@router.get(
    "/sandbox/echo",
    status_code=status.HTTP_200_OK,
    summary="Sandbox echo endpoint for connectivity testing",
)
async def sandbox_echo() -> dict[str, str]:
    """Echo endpoint for sandbox connectivity testing.

    Returns:
        dict[str, str]: Echo response.
    """
    return {"status": "ok", "message": "IAra sandbox echo"}
