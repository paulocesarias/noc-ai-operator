"""Tests for event processor."""

import pytest

from src.core.event_processor import EventProcessor
from src.core.models import (
    ActionStatus,
    ActionType,
    Event,
    EventSeverity,
    EventSource,
)


@pytest.fixture
def processor():
    """Create event processor fixture."""
    return EventProcessor()


@pytest.fixture
def sample_event():
    """Create sample event fixture."""
    return Event(
        id="test-event-001",
        source=EventSource.ALERTMANAGER,
        severity=EventSeverity.WARNING,
        title="Pod CrashLoopBackOff",
        description="Pod my-app-xyz is in CrashLoopBackOff",
        labels={
            "alertname": "PodCrashLoopBackOff",
            "namespace": "production",
            "pod": "my-app-xyz",
        },
    )


@pytest.mark.asyncio
async def test_submit_event(processor, sample_event):
    """Test event submission."""
    event_id = await processor.submit_event(sample_event)
    assert event_id == "test-event-001"
    assert processor.get_event(event_id) is not None


@pytest.mark.asyncio
async def test_list_events(processor, sample_event):
    """Test listing events."""
    await processor.submit_event(sample_event)
    events = processor.list_events()
    assert len(events) == 1
    assert events[0].id == "test-event-001"


def test_register_handler(processor):
    """Test action handler registration."""

    async def mock_handler(action):
        return {"success": True}

    processor.register_handler(ActionType.K8S_RESTART_POD, mock_handler)
    assert ActionType.K8S_RESTART_POD.value in processor.action_handlers


@pytest.mark.asyncio
async def test_event_generates_id(processor):
    """Test that events without ID get one generated."""
    event = Event(
        id="",
        source=EventSource.SYSLOG,
        severity=EventSeverity.INFO,
        title="Test Event",
        description="Test description",
    )
    event_id = await processor.submit_event(event)
    assert event_id != ""
    assert len(event_id) == 36  # UUID length
