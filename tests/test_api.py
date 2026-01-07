"""Tests for API endpoints."""

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app


@pytest.fixture
def client():
    """Create test client fixture."""
    app = create_app()
    return TestClient(app)


def test_health_check(client):
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readiness_check(client):
    """Test readiness endpoint."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_create_event(client):
    """Test event creation."""
    event_data = {
        "source": "alertmanager",
        "severity": "warning",
        "title": "Test Alert",
        "description": "This is a test alert",
        "labels": {"env": "test"},
    }
    response = client.post("/api/v1/events", json=event_data)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["status"] == "accepted"


def test_list_events(client):
    """Test listing events."""
    response = client.get("/api/v1/events")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "events" in data


def test_alertmanager_webhook(client):
    """Test AlertManager webhook."""
    payload = {
        "alerts": [
            {
                "labels": {
                    "alertname": "HighMemoryUsage",
                    "severity": "warning",
                    "namespace": "production",
                },
                "annotations": {
                    "description": "Memory usage is above 80%",
                },
            }
        ]
    }
    response = client.post("/api/v1/webhook/alertmanager", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["received"] == 1
    assert len(data["event_ids"]) == 1


def test_get_nonexistent_event(client):
    """Test getting a non-existent event."""
    response = client.get("/api/v1/events/nonexistent-id")
    assert response.status_code == 404


def test_list_actions(client):
    """Test listing actions."""
    response = client.get("/api/v1/actions")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "actions" in data
