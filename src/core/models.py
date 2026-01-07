"""Core data models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventSeverity(str, Enum):
    """Event severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class EventSource(str, Enum):
    """Event source types."""

    ALERTMANAGER = "alertmanager"
    SYSLOG = "syslog"
    SNMP = "snmp"
    PROMETHEUS = "prometheus"
    CUSTOM = "custom"


class ActionType(str, Enum):
    """Remediation action types."""

    K8S_RESTART_POD = "k8s_restart_pod"
    K8S_SCALE_DEPLOYMENT = "k8s_scale_deployment"
    K8S_ROLLBACK = "k8s_rollback"
    ANSIBLE_PLAYBOOK = "ansible_playbook"
    SSH_COMMAND = "ssh_command"
    SNMP_SET = "snmp_set"
    ESCALATE = "escalate"
    NO_ACTION = "no_action"


class ActionStatus(str, Enum):
    """Action execution status."""

    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"


class Event(BaseModel):
    """Unified event model."""

    id: str = Field(default_factory=lambda: "")
    source: EventSource
    severity: EventSeverity
    title: str
    description: str
    labels: dict[str, str] = Field(default_factory=dict)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AIAnalysis(BaseModel):
    """AI analysis result."""

    event_id: str
    summary: str
    root_cause: str | None = None
    suggested_actions: list[ActionType]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    requires_approval: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RemediationAction(BaseModel):
    """Remediation action to execute."""

    id: str = Field(default_factory=lambda: "")
    event_id: str
    action_type: ActionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    confidence: float = Field(ge=0.0, le=1.0)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    executed_at: datetime | None = None
