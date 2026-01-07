"""Approval workflow service for remediation actions."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog

from src.core.config import settings
from src.core.models import ActionType, AIAnalysis, Event, RemediationAction

logger = structlog.get_logger()


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUTO_APPROVED = "auto_approved"


@dataclass
class ApprovalConfig:
    """Configuration for approval workflow."""

    # Timeout for approval requests (minutes)
    timeout_minutes: int = 30

    # Auto-approve actions below this severity
    auto_approve_severity: str = "info"

    # Action types that always require approval
    always_require_approval: list[ActionType] = field(
        default_factory=lambda: [
            ActionType.K8S_ROLLBACK,
            ActionType.SSH_COMMAND,
        ]
    )

    # Action types that can be auto-approved with high confidence
    auto_approvable: list[ActionType] = field(
        default_factory=lambda: [
            ActionType.K8S_RESTART_POD,
            ActionType.K8S_SCALE_DEPLOYMENT,
            ActionType.NO_ACTION,
        ]
    )

    # Minimum confidence for auto-approval
    auto_approve_confidence: float = 0.85

    # Slack configuration
    slack_enabled: bool = True
    slack_channel: str = "#noc-alerts"

    # Email configuration
    email_enabled: bool = False
    email_recipients: list[str] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    """A request for approval of a remediation action."""

    id: str
    action: RemediationAction
    event: Event
    analysis: AIAnalysis
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    approved_by: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    slack_message_ts: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalResponse:
    """Response from an approval decision."""

    request_id: str
    approved: bool
    responder: str
    reason: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ApprovalService:
    """Service for managing approval workflows."""

    def __init__(
        self,
        config: ApprovalConfig | None = None,
        notifier: Any = None,
    ) -> None:
        self.config = config or ApprovalConfig()
        self._notifier = notifier
        self._pending_requests: dict[str, ApprovalRequest] = {}
        self._approval_callbacks: list[Callable[[ApprovalRequest], Awaitable[None]]] = []
        self._rejection_callbacks: list[Callable[[ApprovalRequest], Awaitable[None]]] = []
        self._expiry_task: asyncio.Task | None = None

    def set_notifier(self, notifier: Any) -> None:
        """Set the notification handler (e.g., Slack)."""
        self._notifier = notifier

    def on_approval(
        self, callback: Callable[[ApprovalRequest], Awaitable[None]]
    ) -> None:
        """Register a callback for when an action is approved."""
        self._approval_callbacks.append(callback)

    def on_rejection(
        self, callback: Callable[[ApprovalRequest], Awaitable[None]]
    ) -> None:
        """Register a callback for when an action is rejected."""
        self._rejection_callbacks.append(callback)

    async def start(self) -> None:
        """Start the approval service (expiry checker)."""
        self._expiry_task = asyncio.create_task(self._check_expiry_loop())
        logger.info("Approval service started")

    async def stop(self) -> None:
        """Stop the approval service."""
        if self._expiry_task:
            self._expiry_task.cancel()
            try:
                await self._expiry_task
            except asyncio.CancelledError:
                pass
        logger.info("Approval service stopped")

    async def request_approval(
        self,
        action: RemediationAction,
        event: Event,
        analysis: AIAnalysis,
    ) -> ApprovalRequest:
        """Create an approval request for an action."""
        # Check if auto-approval is possible
        auto_approve, reason = self._check_auto_approve(action, event, analysis)

        request = ApprovalRequest(
            id=str(uuid4()),
            action=action,
            event=event,
            analysis=analysis,
            status=ApprovalStatus.AUTO_APPROVED if auto_approve else ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(minutes=self.config.timeout_minutes),
        )

        if auto_approve:
            logger.info(
                "Action auto-approved",
                request_id=request.id,
                action_type=action.action_type.value,
                reason=reason,
            )
            request.approved_by = "system"
            request.metadata["auto_approve_reason"] = reason

            # Trigger approval callbacks
            for callback in self._approval_callbacks:
                try:
                    await callback(request)
                except Exception as e:
                    logger.error("Approval callback error", error=str(e))
        else:
            # Store pending request
            self._pending_requests[request.id] = request

            # Send notification
            if self._notifier:
                try:
                    message_ts = await self._notifier.send_approval_request(request)
                    request.slack_message_ts = message_ts
                except Exception as e:
                    logger.error("Failed to send approval notification", error=str(e))

            logger.info(
                "Approval requested",
                request_id=request.id,
                action_type=action.action_type.value,
                expires_at=request.expires_at.isoformat() if request.expires_at else None,
            )

        return request

    def _check_auto_approve(
        self,
        action: RemediationAction,
        event: Event,
        analysis: AIAnalysis,
    ) -> tuple[bool, str]:
        """Check if an action can be auto-approved."""
        # Never auto-approve certain action types
        if action.action_type in self.config.always_require_approval:
            return False, f"Action type {action.action_type.value} always requires approval"

        # Check if analysis says approval is required
        if analysis.requires_approval:
            return False, "AI analysis recommends manual approval"

        # Check if action type is auto-approvable
        if action.action_type not in self.config.auto_approvable:
            return False, f"Action type {action.action_type.value} is not auto-approvable"

        # Check confidence threshold
        if analysis.confidence < self.config.auto_approve_confidence:
            return (
                False,
                f"Confidence {analysis.confidence:.2f} below threshold {self.config.auto_approve_confidence}",
            )

        # Check severity
        if event.severity.value == "critical":
            return False, "Critical severity requires manual approval"

        # Auto-approve info severity
        if event.severity.value == self.config.auto_approve_severity:
            return True, f"Auto-approved due to {event.severity.value} severity"

        # Check runbook auto-remediation flag
        if analysis.runbook_id:
            # Runbook allows auto-remediation (checked by analyzer)
            return True, f"Auto-approved based on runbook {analysis.runbook_id}"

        # High confidence auto-approval
        if analysis.confidence >= self.config.auto_approve_confidence:
            return True, f"Auto-approved due to high confidence ({analysis.confidence:.2f})"

        return False, "Default: requires approval"

    async def approve(
        self, request_id: str, approver: str, reason: str | None = None
    ) -> ApprovalResponse:
        """Approve a pending request."""
        request = self._pending_requests.get(request_id)
        if not request:
            raise ValueError(f"Approval request {request_id} not found")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Request {request_id} is not pending (status: {request.status})")

        request.status = ApprovalStatus.APPROVED
        request.approved_by = approver

        # Remove from pending
        del self._pending_requests[request_id]

        # Update notification if available
        if self._notifier and request.slack_message_ts:
            try:
                await self._notifier.update_approval_status(
                    request, approved=True, responder=approver
                )
            except Exception as e:
                logger.error("Failed to update approval notification", error=str(e))

        # Trigger callbacks
        for callback in self._approval_callbacks:
            try:
                await callback(request)
            except Exception as e:
                logger.error("Approval callback error", error=str(e))

        logger.info(
            "Action approved",
            request_id=request_id,
            approver=approver,
            action_type=request.action.action_type.value,
        )

        return ApprovalResponse(
            request_id=request_id,
            approved=True,
            responder=approver,
            reason=reason,
        )

    async def reject(
        self, request_id: str, rejector: str, reason: str | None = None
    ) -> ApprovalResponse:
        """Reject a pending request."""
        request = self._pending_requests.get(request_id)
        if not request:
            raise ValueError(f"Approval request {request_id} not found")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Request {request_id} is not pending (status: {request.status})")

        request.status = ApprovalStatus.REJECTED
        request.rejected_by = rejector
        request.rejection_reason = reason

        # Remove from pending
        del self._pending_requests[request_id]

        # Update notification if available
        if self._notifier and request.slack_message_ts:
            try:
                await self._notifier.update_approval_status(
                    request, approved=False, responder=rejector, reason=reason
                )
            except Exception as e:
                logger.error("Failed to update rejection notification", error=str(e))

        # Trigger callbacks
        for callback in self._rejection_callbacks:
            try:
                await callback(request)
            except Exception as e:
                logger.error("Rejection callback error", error=str(e))

        logger.info(
            "Action rejected",
            request_id=request_id,
            rejector=rejector,
            reason=reason,
            action_type=request.action.action_type.value,
        )

        return ApprovalResponse(
            request_id=request_id,
            approved=False,
            responder=rejector,
            reason=reason,
        )

    def get_pending_requests(self) -> list[ApprovalRequest]:
        """Get all pending approval requests."""
        return list(self._pending_requests.values())

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get a specific approval request."""
        return self._pending_requests.get(request_id)

    async def _check_expiry_loop(self) -> None:
        """Background task to check for expired approval requests."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._expire_old_requests()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Expiry check error", error=str(e))

    async def _expire_old_requests(self) -> None:
        """Expire requests that have passed their deadline."""
        now = datetime.utcnow()
        expired = []

        for request_id, request in list(self._pending_requests.items()):
            if request.expires_at and now > request.expires_at:
                request.status = ApprovalStatus.EXPIRED
                expired.append(request_id)
                del self._pending_requests[request_id]

                # Update notification
                if self._notifier and request.slack_message_ts:
                    try:
                        await self._notifier.update_approval_status(
                            request, approved=False, responder="system", reason="Request expired"
                        )
                    except Exception as e:
                        logger.error("Failed to update expiry notification", error=str(e))

                logger.warning(
                    "Approval request expired",
                    request_id=request_id,
                    action_type=request.action.action_type.value,
                )

        if expired:
            logger.info("Expired approval requests", count=len(expired))


# Singleton instance
_approval_service: ApprovalService | None = None


def get_approval_service() -> ApprovalService:
    """Get or create the global approval service."""
    global _approval_service
    if _approval_service is None:
        config = ApprovalConfig(
            timeout_minutes=getattr(settings, "approval_timeout_minutes", 30),
            slack_enabled=getattr(settings, "slack_enabled", True),
            slack_channel=getattr(settings, "slack_approval_channel", "#noc-alerts"),
        )
        _approval_service = ApprovalService(config=config)
    return _approval_service
