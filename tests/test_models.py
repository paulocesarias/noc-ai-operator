"""Tests for core models."""

from src.core.models import (
    ActionType,
    Event,
    EventSeverity,
    EventSource,
)


def test_event_creation():
    """Test creating an event."""
    event = Event(
        id="test-123",
        source=EventSource.ALERTMANAGER,
        severity=EventSeverity.WARNING,
        title="Test Alert",
        description="This is a test alert",
        labels={"env": "test"},
    )

    assert event.id == "test-123"
    assert event.source == EventSource.ALERTMANAGER
    assert event.severity == EventSeverity.WARNING
    assert event.labels["env"] == "test"


def test_action_types():
    """Test action type enum."""
    assert ActionType.K8S_RESTART_POD.value == "k8s_restart_pod"
    assert ActionType.ESCALATE.value == "escalate"
