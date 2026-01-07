"""Health check endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}


@router.get("/ready")
async def readiness_check() -> dict:
    """Readiness check endpoint."""
    return {"status": "ready"}
