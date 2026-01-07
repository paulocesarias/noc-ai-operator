"""Event ingestion endpoints."""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from src.core.models import Event, EventSeverity, EventSource

router = APIRouter()


@router.post("/events")
async def create_event(event: Event) -> dict:
    """Create a new event for processing."""
    if not event.id:
        event.id = str(uuid4())

    # TODO: Submit to event processor queue
    return {"id": event.id, "status": "accepted"}


@router.post("/webhook/alertmanager")
async def alertmanager_webhook(payload: dict[str, Any]) -> dict:
    """Receive AlertManager webhooks."""
    events = []

    for alert in payload.get("alerts", []):
        severity_map = {
            "critical": EventSeverity.CRITICAL,
            "warning": EventSeverity.WARNING,
            "info": EventSeverity.INFO,
        }

        event = Event(
            id=str(uuid4()),
            source=EventSource.ALERTMANAGER,
            severity=severity_map.get(
                alert.get("labels", {}).get("severity", "info"),
                EventSeverity.INFO,
            ),
            title=alert.get("labels", {}).get("alertname", "Unknown Alert"),
            description=alert.get("annotations", {}).get("description", ""),
            labels=alert.get("labels", {}),
            raw_data=alert,
        )
        events.append(event)
        # TODO: Submit to event processor queue

    return {"received": len(events)}


@router.get("/events/{event_id}")
async def get_event(event_id: str) -> dict:
    """Get event details."""
    # TODO: Implement event retrieval from database
    raise HTTPException(status_code=404, detail="Event not found")
