"""Dashboard routes for the monitoring UI."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.core.event_processor import get_event_processor

router = APIRouter()

# Templates directory
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Main dashboard page."""
    processor = get_event_processor()
    events = processor.list_events(limit=50)
    actions = processor.list_actions(limit=50)

    # Calculate stats
    stats = {
        "total_events": len(processor._events),
        "total_actions": len(processor._actions),
        "pending_actions": sum(1 for a in processor._actions.values() if a.status.value == "pending"),
        "successful_actions": sum(1 for a in processor._actions.values() if a.status.value == "success"),
        "failed_actions": sum(1 for a in processor._actions.values() if a.status.value == "failed"),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "events": events,
            "actions": actions,
            "stats": stats,
        },
    )


@router.get("/events", response_class=HTMLResponse)
async def events_list(request: Request):
    """Events list partial (for HTMX updates)."""
    processor = get_event_processor()
    events = processor.list_events(limit=50)

    return templates.TemplateResponse(
        "partials/events_list.html",
        {"request": request, "events": events},
    )


@router.get("/events/{event_id}", response_class=HTMLResponse)
async def event_detail(request: Request, event_id: str):
    """Event detail view."""
    processor = get_event_processor()
    event = processor.get_event(event_id)
    analysis = processor.get_analysis(event_id)

    # Get related actions
    related_actions = [a for a in processor._actions.values() if a.event_id == event_id]

    return templates.TemplateResponse(
        "event_detail.html",
        {
            "request": request,
            "event": event,
            "analysis": analysis,
            "actions": related_actions,
        },
    )


@router.get("/actions", response_class=HTMLResponse)
async def actions_list(request: Request):
    """Actions list partial (for HTMX updates)."""
    processor = get_event_processor()
    actions = processor.list_actions(limit=50)

    return templates.TemplateResponse(
        "partials/actions_list.html",
        {"request": request, "actions": actions},
    )


@router.get("/actions/{action_id}", response_class=HTMLResponse)
async def action_detail(request: Request, action_id: str):
    """Action detail view."""
    processor = get_event_processor()
    action = processor.get_action(action_id)
    event = processor.get_event(action.event_id) if action else None

    return templates.TemplateResponse(
        "action_detail.html",
        {
            "request": request,
            "action": action,
            "event": event,
        },
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    """Stats partial (for HTMX updates)."""
    processor = get_event_processor()

    stats = {
        "total_events": len(processor._events),
        "total_actions": len(processor._actions),
        "pending_actions": sum(1 for a in processor._actions.values() if a.status.value == "pending"),
        "successful_actions": sum(1 for a in processor._actions.values() if a.status.value == "success"),
        "failed_actions": sum(1 for a in processor._actions.values() if a.status.value == "failed"),
    }

    return templates.TemplateResponse(
        "partials/stats.html",
        {"request": request, "stats": stats},
    )
