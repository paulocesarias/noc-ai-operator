"""API routes for approval workflows."""


import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.workflows.approval import (
    ApprovalRequest,
    get_approval_service,
)

logger = structlog.get_logger()
router = APIRouter()


class ApprovalRequestResponse(BaseModel):
    """Response model for an approval request."""

    id: str
    action_id: str
    action_type: str
    event_id: str
    event_title: str
    event_severity: str
    analysis_summary: str
    confidence: float
    status: str
    created_at: str
    expires_at: str | None
    approved_by: str | None
    rejected_by: str | None
    rejection_reason: str | None


class ApproveRequest(BaseModel):
    """Request model for approving an action."""

    approver: str = Field(..., description="Name or ID of the approver")
    reason: str | None = Field(None, description="Optional reason for approval")


class RejectRequest(BaseModel):
    """Request model for rejecting an action."""

    rejector: str = Field(..., description="Name or ID of the rejector")
    reason: str | None = Field(None, description="Optional reason for rejection")


class ApprovalDecisionResponse(BaseModel):
    """Response model for an approval decision."""

    request_id: str
    approved: bool
    responder: str
    reason: str | None
    timestamp: str


class ApprovalStats(BaseModel):
    """Statistics about approval requests."""

    total_pending: int
    total_approved: int
    total_rejected: int
    total_expired: int
    auto_approved: int


def request_to_response(request: ApprovalRequest) -> ApprovalRequestResponse:
    """Convert an ApprovalRequest to a response model."""
    return ApprovalRequestResponse(
        id=request.id,
        action_id=request.action.id,
        action_type=request.action.action_type.value,
        event_id=request.event.id,
        event_title=request.event.title,
        event_severity=request.event.severity.value,
        analysis_summary=request.analysis.summary,
        confidence=request.analysis.confidence,
        status=request.status.value,
        created_at=request.created_at.isoformat(),
        expires_at=request.expires_at.isoformat() if request.expires_at else None,
        approved_by=request.approved_by,
        rejected_by=request.rejected_by,
        rejection_reason=request.rejection_reason,
    )


@router.get("/pending", response_model=list[ApprovalRequestResponse])
async def get_pending_approvals() -> list[ApprovalRequestResponse]:
    """Get all pending approval requests."""
    service = get_approval_service()
    requests = service.get_pending_requests()
    return [request_to_response(r) for r in requests]


@router.get("/{request_id}", response_model=ApprovalRequestResponse)
async def get_approval_request(request_id: str) -> ApprovalRequestResponse:
    """Get a specific approval request by ID."""
    service = get_approval_service()
    request = service.get_request(request_id)

    if not request:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return request_to_response(request)


@router.post("/{request_id}/approve", response_model=ApprovalDecisionResponse)
async def approve_action(request_id: str, body: ApproveRequest) -> ApprovalDecisionResponse:
    """Approve a pending action."""
    service = get_approval_service()

    try:
        response = await service.approve(
            request_id=request_id,
            approver=body.approver,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ApprovalDecisionResponse(
        request_id=response.request_id,
        approved=response.approved,
        responder=response.responder,
        reason=response.reason,
        timestamp=response.timestamp.isoformat(),
    )


@router.post("/{request_id}/reject", response_model=ApprovalDecisionResponse)
async def reject_action(request_id: str, body: RejectRequest) -> ApprovalDecisionResponse:
    """Reject a pending action."""
    service = get_approval_service()

    try:
        response = await service.reject(
            request_id=request_id,
            rejector=body.rejector,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ApprovalDecisionResponse(
        request_id=response.request_id,
        approved=response.approved,
        responder=response.responder,
        reason=response.reason,
        timestamp=response.timestamp.isoformat(),
    )


@router.get("/stats/summary", response_model=ApprovalStats)
async def get_approval_stats() -> ApprovalStats:
    """Get approval statistics summary."""
    service = get_approval_service()
    pending = service.get_pending_requests()

    # In a real implementation, we'd track historical stats
    # For now, just return current pending count
    return ApprovalStats(
        total_pending=len(pending),
        total_approved=0,  # Would need persistence
        total_rejected=0,
        total_expired=0,
        auto_approved=0,
    )
