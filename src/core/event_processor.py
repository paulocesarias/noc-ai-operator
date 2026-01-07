"""Event processor - orchestrates the alert processing pipeline."""

import asyncio
from collections.abc import Callable

import structlog

from src.ai.llm.analyzer import AlertAnalyzer
from src.core.models import ActionStatus, Event, RemediationAction

logger = structlog.get_logger()


class EventProcessor:
    """Processes incoming events through the AI pipeline."""

    def __init__(self) -> None:
        self.analyzer = AlertAnalyzer()
        self.action_handlers: dict[str, Callable] = {}
        self._running = False
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    async def start(self) -> None:
        """Start the event processor."""
        self._running = True
        logger.info("Event processor started")
        asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        """Stop the event processor."""
        self._running = False
        logger.info("Event processor stopped")

    async def submit_event(self, event: Event) -> None:
        """Submit an event for processing."""
        await self._queue.put(event)
        logger.info("Event submitted", event_id=event.id, source=event.source)

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
        logger.info(
            "AI analysis complete",
            event_id=event.id,
            confidence=analysis.confidence,
            actions=analysis.suggested_actions,
        )

        # Create remediation actions
        for action_type in analysis.suggested_actions:
            action = RemediationAction(
                event_id=event.id,
                action_type=action_type,
                confidence=analysis.confidence,
                status=(
                    ActionStatus.PENDING
                    if analysis.requires_approval
                    else ActionStatus.APPROVED
                ),
            )

            if action.status == ActionStatus.APPROVED:
                await self._execute_action(action)
            else:
                logger.info(
                    "Action requires approval",
                    action_id=action.id,
                    action_type=action_type,
                )

    async def _execute_action(self, action: RemediationAction) -> None:
        """Execute a remediation action."""
        logger.info("Executing action", action_id=action.id, action_type=action.action_type)

        handler = self.action_handlers.get(action.action_type.value)
        if handler:
            try:
                action.status = ActionStatus.EXECUTING
                result = await handler(action)
                action.status = ActionStatus.SUCCESS
                action.result = result
                logger.info("Action completed", action_id=action.id)
            except Exception as e:
                action.status = ActionStatus.FAILED
                action.error = str(e)
                logger.error("Action failed", action_id=action.id, error=str(e))
        else:
            logger.warning("No handler for action type", action_type=action.action_type)
