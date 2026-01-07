"""Slack integration for approval notifications."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

from src.core.config import settings

logger = structlog.get_logger()

# Optional Slack SDK import
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    logger.warning("slack_sdk not installed, Slack notifications disabled")

# Thread pool for blocking Slack API calls
_executor = ThreadPoolExecutor(max_workers=3)


class SlackNotifier:
    """Slack notification handler for approval workflows."""

    def __init__(
        self,
        token: str | None = None,
        channel: str = "#noc-alerts",
        bot_name: str = "NOC AI Operator",
    ) -> None:
        self.token = token or getattr(settings, "slack_token", None)
        self.channel = channel
        self.bot_name = bot_name
        self._client: Any = None

        if SLACK_AVAILABLE and self.token:
            self._client = WebClient(token=self.token)
            logger.info("Slack notifier initialized", channel=channel)
        else:
            logger.warning("Slack notifier not initialized (missing token or SDK)")

    @property
    def is_available(self) -> bool:
        """Check if Slack notifications are available."""
        return self._client is not None

    async def send_approval_request(self, request: Any) -> str | None:
        """Send an approval request notification to Slack."""
        if not self.is_available:
            logger.debug("Slack not available, skipping notification")
            return None

        blocks = self._build_approval_blocks(request)

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                _executor,
                lambda: self._client.chat_postMessage(
                    channel=self.channel,
                    text=f"Approval required: {request.action.action_type.value}",
                    blocks=blocks,
                    username=self.bot_name,
                ),
            )
            message_ts = response.get("ts")
            logger.info(
                "Approval request sent to Slack",
                channel=self.channel,
                message_ts=message_ts,
            )
            return message_ts
        except Exception as e:
            logger.error("Failed to send Slack notification", error=str(e))
            return None

    async def update_approval_status(
        self,
        request: Any,
        approved: bool,
        responder: str,
        reason: str | None = None,
    ) -> bool:
        """Update an approval request message with the result."""
        if not self.is_available or not request.slack_message_ts:
            return False

        blocks = self._build_result_blocks(request, approved, responder, reason)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _executor,
                lambda: self._client.chat_update(
                    channel=self.channel,
                    ts=request.slack_message_ts,
                    text=f"Action {'approved' if approved else 'rejected'}: {request.action.action_type.value}",
                    blocks=blocks,
                ),
            )
            logger.info(
                "Approval status updated in Slack",
                approved=approved,
                responder=responder,
            )
            return True
        except Exception as e:
            logger.error("Failed to update Slack message", error=str(e))
            return False

    async def send_action_result(
        self,
        action: Any,
        success: bool,
        details: str | None = None,
    ) -> str | None:
        """Send an action execution result notification."""
        if not self.is_available:
            return None

        color = "#36a64f" if success else "#ff0000"
        status_emoji = ":white_check_mark:" if success else ":x:"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} Action {'Completed' if success else 'Failed'}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Action:*\n{action.action_type.value}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Status:*\n{'Success' if success else 'Failed'}",
                    },
                ],
            },
        ]

        if details:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details:*\n```{details[:2000]}```",
                },
            })

        if action.error:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n```{action.error[:1000]}```",
                },
            })

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                _executor,
                lambda: self._client.chat_postMessage(
                    channel=self.channel,
                    text=f"Action {'completed' if success else 'failed'}: {action.action_type.value}",
                    blocks=blocks,
                    username=self.bot_name,
                ),
            )
            return response.get("ts")
        except Exception as e:
            logger.error("Failed to send action result to Slack", error=str(e))
            return None

    async def send_alert(
        self,
        event: Any,
        analysis: Any | None = None,
    ) -> str | None:
        """Send an alert notification to Slack."""
        if not self.is_available:
            return None

        severity_colors = {
            "critical": "#ff0000",
            "warning": "#ffcc00",
            "info": "#36a64f",
        }
        color = severity_colors.get(event.severity.value, "#808080")
        severity_emoji = {
            "critical": ":rotating_light:",
            "warning": ":warning:",
            "info": ":information_source:",
        }.get(event.severity.value, ":bell:")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} {event.title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:*\n{event.severity.value.upper()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Source:*\n{event.source.value}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{event.description[:500]}",
                },
            },
        ]

        if analysis:
            blocks.extend([
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*AI Analysis:*\n{analysis.summary}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Confidence:*\n{analysis.confidence:.0%}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Actions:*\n{', '.join(a.value for a in analysis.suggested_actions)}",
                        },
                    ],
                },
            ])

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                _executor,
                lambda: self._client.chat_postMessage(
                    channel=self.channel,
                    text=f"Alert: {event.title}",
                    blocks=blocks,
                    username=self.bot_name,
                ),
            )
            return response.get("ts")
        except Exception as e:
            logger.error("Failed to send alert to Slack", error=str(e))
            return None

    def _build_approval_blocks(self, request: Any) -> list[dict[str, Any]]:
        """Build Slack blocks for an approval request."""
        event = request.event
        action = request.action
        analysis = request.analysis

        severity_emoji = {
            "critical": ":rotating_light:",
            "warning": ":warning:",
            "info": ":information_source:",
        }.get(event.severity.value, ":bell:")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} Approval Required",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Alert:* {event.title}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:*\n{event.severity.value.upper()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Source:*\n{event.source.value}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Action:*\n`{action.action_type.value}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Confidence:*\n{analysis.confidence:.0%}",
                    },
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Analysis:*\n{analysis.summary}",
                },
            },
        ]

        if analysis.root_cause:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Root Cause:*\n{analysis.root_cause}",
                },
            })

        # Add action buttons
        blocks.extend([
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"approval_{request.id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": ":white_check_mark: Approve",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": request.id,
                        "action_id": "approve_action",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": ":x: Reject",
                            "emoji": True,
                        },
                        "style": "danger",
                        "value": request.id,
                        "action_id": "reject_action",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Request ID: `{request.id}` | Expires: {request.expires_at.strftime('%Y-%m-%d %H:%M UTC') if request.expires_at else 'Never'}",
                    },
                ],
            },
        ])

        return blocks

    def _build_result_blocks(
        self,
        request: Any,
        approved: bool,
        responder: str,
        reason: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build Slack blocks for an approval result."""
        status_emoji = ":white_check_mark:" if approved else ":x:"
        status_text = "Approved" if approved else "Rejected"

        event = request.event
        action = request.action

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} Action {status_text}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Alert:* {event.title}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Action:*\n`{action.action_type.value}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*{status_text} by:*\n{responder}",
                    },
                ],
            },
        ]

        if reason:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reason:*\n{reason}",
                },
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Request ID: `{request.id}`",
                },
            ],
        })

        return blocks


# Singleton instance
_slack_notifier: SlackNotifier | None = None


def get_slack_notifier() -> SlackNotifier:
    """Get or create the global Slack notifier."""
    global _slack_notifier
    if _slack_notifier is None:
        _slack_notifier = SlackNotifier(
            token=getattr(settings, "slack_token", None),
            channel=getattr(settings, "slack_approval_channel", "#noc-alerts"),
        )
    return _slack_notifier
