"""Event ingestion endpoints."""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from src.core.event_processor import get_event_processor
from src.core.models import Event, EventSeverity, EventSource

router = APIRouter()


@router.post("/events")
async def create_event(event: Event) -> dict:
    """Create a new event for processing."""
    processor = get_event_processor()
    event_id = await processor.submit_event(event)
    return {"id": event_id, "status": "accepted"}


@router.get("/events")
async def list_events(limit: int = 100) -> dict:
    """List recent events."""
    processor = get_event_processor()
    events = processor.list_events(limit)
    return {
        "count": len(events),
        "events": [
            {
                "id": e.id,
                "source": e.source.value,
                "severity": e.severity.value,
                "title": e.title,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str) -> dict:
    """Get event details."""
    processor = get_event_processor()
    event = processor.get_event(event_id)

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    analysis = processor.get_analysis(event_id)

    return {
        "id": event.id,
        "source": event.source.value,
        "severity": event.severity.value,
        "title": event.title,
        "description": event.description,
        "labels": event.labels,
        "timestamp": event.timestamp.isoformat(),
        "analysis": analysis,
    }


@router.post("/webhook/alertmanager")
async def alertmanager_webhook(payload: dict[str, Any]) -> dict:
    """Receive AlertManager webhooks."""
    processor = get_event_processor()
    event_ids = []

    severity_map = {
        "critical": EventSeverity.CRITICAL,
        "warning": EventSeverity.WARNING,
        "info": EventSeverity.INFO,
    }

    for alert in payload.get("alerts", []):
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
        event_id = await processor.submit_event(event)
        event_ids.append(event_id)

    return {"received": len(event_ids), "event_ids": event_ids}


@router.get("/actions")
async def list_actions(limit: int = 100) -> dict:
    """List recent remediation actions."""
    processor = get_event_processor()
    actions = processor.list_actions(limit)
    return {
        "count": len(actions),
        "actions": [
            {
                "id": a.id,
                "event_id": a.event_id,
                "action_type": a.action_type.value,
                "status": a.status.value,
                "confidence": a.confidence,
                "created_at": a.created_at.isoformat(),
                "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            }
            for a in actions
        ],
    }


@router.get("/actions/{action_id}")
async def get_action(action_id: str) -> dict:
    """Get action details."""
    processor = get_event_processor()
    action = processor.get_action(action_id)

    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    return {
        "id": action.id,
        "event_id": action.event_id,
        "action_type": action.action_type.value,
        "status": action.status.value,
        "parameters": action.parameters,
        "confidence": action.confidence,
        "result": action.result,
        "error": action.error,
        "created_at": action.created_at.isoformat(),
        "executed_at": action.executed_at.isoformat() if action.executed_at else None,
    }


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str) -> dict:
    """Approve a pending action."""
    processor = get_event_processor()
    success = await processor.approve_action(action_id)

    if not success:
        raise HTTPException(status_code=400, detail="Action cannot be approved")

    return {"status": "approved", "action_id": action_id}


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: str) -> dict:
    """Reject a pending action."""
    processor = get_event_processor()
    success = await processor.reject_action(action_id)

    if not success:
        raise HTTPException(status_code=400, detail="Action cannot be rejected")

    return {"status": "rejected", "action_id": action_id}
