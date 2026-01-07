"""Event processor - orchestrates the alert processing pipeline."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from src.ai.llm.analyzer import AlertAnalyzer
from src.core.models import ActionStatus, ActionType, AIAnalysis, Event, RemediationAction

logger = structlog.get_logger()

# Global event processor instance
_processor: "EventProcessor | None" = None


def get_event_processor() -> "EventProcessor":
    """Get the global event processor instance."""
    global _processor
    if _processor is None:
        _processor = EventProcessor()
    return _processor


class EventProcessor:
    """Processes incoming events through the AI pipeline."""

    def __init__(self) -> None:
        self.analyzer = AlertAnalyzer()
        self.action_handlers: dict[str, Callable] = {}
        self._running = False
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._events: dict[str, Event] = {}
        self._actions: dict[str, RemediationAction] = {}
        self._analyses: dict[str, AIAnalysis] = {}
        self._approval_service: Any = None

    def set_approval_service(self, service: Any) -> None:
        """Set the approval service for workflow integration."""
        self._approval_service = service

        # Register callbacks
        service.on_approval(self._on_action_approved)
        service.on_rejection(self._on_action_rejected)

        logger.info("Approval service connected to event processor")

    async def _on_action_approved(self, request: Any) -> None:
        """Callback when an action is approved via the approval workflow."""
        action = request.action
        if action.id in self._actions:
            self._actions[action.id].status = ActionStatus.APPROVED
            await self._execute_action(self._actions[action.id])

    async def _on_action_rejected(self, request: Any) -> None:
        """Callback when an action is rejected via the approval workflow."""
        action = request.action
        if action.id in self._actions:
            self._actions[action.id].status = ActionStatus.REJECTED

    async def start(self) -> None:
        """Start the event processor."""
        self._running = True
        logger.info("Event processor started")
        asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        """Stop the event processor."""
        self._running = False
        logger.info("Event processor stopped")

    async def submit_event(self, event: Event) -> str:
        """Submit an event for processing."""
        if not event.id:
            event.id = str(uuid4())

        self._events[event.id] = event
        await self._queue.put(event)
        logger.info("Event submitted", event_id=event.id, source=event.source)
        return event.id

    def get_event(self, event_id: str) -> Event | None:
        """Get an event by ID."""
        return self._events.get(event_id)

    def get_action(self, action_id: str) -> RemediationAction | None:
        """Get an action by ID."""
        return self._actions.get(action_id)

    def get_analysis(self, event_id: str) -> AIAnalysis | None:
        """Get analysis for an event."""
        return self._analyses.get(event_id)

    def get_analysis_dict(self, event_id: str) -> dict[str, Any] | None:
        """Get analysis for an event as a dictionary."""
        analysis = self._analyses.get(event_id)
        if analysis:
            return {
                "summary": analysis.summary,
                "root_cause": analysis.root_cause,
                "suggested_actions": [a.value for a in analysis.suggested_actions],
                "confidence": analysis.confidence,
                "reasoning": analysis.reasoning,
                "requires_approval": analysis.requires_approval,
                "runbook_id": analysis.runbook_id,
                "timestamp": analysis.timestamp.isoformat(),
            }
        return None

    def list_events(self, limit: int = 100) -> list[Event]:
        """List recent events."""
        events = list(self._events.values())
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]

    def list_actions(self, limit: int = 100) -> list[RemediationAction]:
        """List recent actions."""
        actions = list(self._actions.values())
        actions.sort(key=lambda a: a.created_at, reverse=True)
        return actions[:limit]

    def get_stats(self) -> dict[str, int]:
        """Get event and action statistics."""
        actions = list(self._actions.values())
        return {
            "total_events": len(self._events),
            "total_actions": len(actions),
            "pending_actions": sum(1 for a in actions if a.status == ActionStatus.PENDING),
            "successful_actions": sum(1 for a in actions if a.status == ActionStatus.SUCCESS),
            "failed_actions": sum(1 for a in actions if a.status == ActionStatus.FAILED),
        }

    def register_handler(self, action_type: ActionType, handler: Callable) -> None:
        """Register an action handler."""
        self.action_handlers[action_type.value] = handler
        logger.info("Registered action handler", action_type=action_type.value)

    async def approve_action(self, action_id: str) -> bool:
        """Approve a pending action (legacy - direct approval)."""
        action = self._actions.get(action_id)
        if action and action.status == ActionStatus.PENDING:
            action.status = ActionStatus.APPROVED
            await self._execute_action(action)
            return True
        return False

    async def reject_action(self, action_id: str) -> bool:
        """Reject a pending action (legacy - direct rejection)."""
        action = self._actions.get(action_id)
        if action and action.status == ActionStatus.PENDING:
            action.status = ActionStatus.REJECTED
            return True
        return False

    async def _process_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Error in processing loop", error=str(e))

    async def _process_event(self, event: Event) -> None:
        """Process a single event."""
        logger.info("Processing event", event_id=event.id, title=event.title)

        # Analyze with AI
        analysis = await self.analyzer.analyze(event)

        # Store analysis
        self._analyses[event.id] = analysis

        logger.info(
            "AI analysis complete",
            event_id=event.id,
            confidence=analysis.confidence,
            actions=[a.value for a in analysis.suggested_actions],
            requires_approval=analysis.requires_approval,
            runbook_id=analysis.runbook_id,
        )

        # Create remediation actions
        for action_type in analysis.suggested_actions:
            action = RemediationAction(
                id=str(uuid4()),
                event_id=event.id,
                action_type=action_type,
                confidence=analysis.confidence,
                status=ActionStatus.PENDING,  # Always start as pending
            )

            self._actions[action.id] = action

            # Route through approval workflow if available
            if self._approval_service:
                await self._approval_service.request_approval(
                    action=action,
                    event=event,
                    analysis=analysis,
                )
            elif not analysis.requires_approval:
                # No approval service and approval not required
                action.status = ActionStatus.APPROVED
                await self._execute_action(action)
            else:
                logger.info(
                    "Action requires approval (no approval service)",
                    action_id=action.id,
                    action_type=action_type.value,
                )

    async def _execute_action(self, action: RemediationAction) -> None:
        """Execute a remediation action."""
        logger.info("Executing action", action_id=action.id, action_type=action.action_type.value)

        handler = self.action_handlers.get(action.action_type.value)
        if handler:
            try:
                action.status = ActionStatus.EXECUTING
                action.executed_at = datetime.utcnow()
                result = await handler(action)
                action.status = ActionStatus.SUCCESS
                action.result = result
                logger.info("Action completed", action_id=action.id)
            except Exception as e:
                action.status = ActionStatus.FAILED
                action.error = str(e)
                logger.error("Action failed", action_id=action.id, error=str(e))
        else:
            logger.warning("No handler for action type", action_type=action.action_type.value)
            # Mark as success for no_action and escalate types
            if action.action_type in [ActionType.NO_ACTION, ActionType.ESCALATE]:
                action.status = ActionStatus.SUCCESS
                action.result = {"message": f"Action type {action.action_type.value} logged"}
