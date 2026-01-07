"""Alert analyzer using Claude API."""

import json

import anthropic
import structlog

from src.core.config import settings
from src.core.models import ActionType, AIAnalysis, Event

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are an expert NOC (Network Operations Center) operator AI.
Your job is to analyze infrastructure alerts and suggest remediation actions.

When analyzing an alert, consider:
1. The severity and potential impact
2. Common root causes for this type of issue
3. Safe remediation actions that can be automated
4. Whether human approval is needed for risky operations

Available remediation actions:
- k8s_restart_pod: Restart a Kubernetes pod
- k8s_scale_deployment: Scale a deployment up/down
- k8s_rollback: Rollback to previous deployment version
- ansible_playbook: Run an Ansible playbook
- ssh_command: Execute SSH command on target host
- snmp_set: Set SNMP OID value
- escalate: Escalate to human operator
- no_action: No action needed (informational alert)

Respond with a JSON object containing:
{
    "summary": "Brief summary of the issue",
    "root_cause": "Likely root cause if determinable",
    "suggested_actions": ["action_type1", "action_type2"],
    "confidence": 0.0-1.0,
    "reasoning": "Explanation of your analysis",
    "requires_approval": true/false
}

Set requires_approval=true for:
- Destructive operations
- Actions affecting production
- Confidence below 0.7
- Unfamiliar alert patterns
"""


class AlertAnalyzer:
    """Analyzes alerts using Claude API."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def analyze(self, event: Event) -> AIAnalysis:
        """Analyze an event and suggest remediation actions."""
        prompt = self._build_prompt(event)

        try:
            response = self.client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            result = self._parse_response(response.content[0].text)

            return AIAnalysis(
                event_id=event.id,
                summary=result.get("summary", ""),
                root_cause=result.get("root_cause"),
                suggested_actions=[
                    ActionType(a) for a in result.get("suggested_actions", ["no_action"])
                ],
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
                requires_approval=result.get("requires_approval", True),
            )

        except Exception as e:
            logger.error("AI analysis failed", error=str(e))
            return AIAnalysis(
                event_id=event.id,
                summary="Analysis failed",
                suggested_actions=[ActionType.ESCALATE],
                confidence=0.0,
                reasoning=f"AI analysis error: {e}",
                requires_approval=True,
            )

    def _build_prompt(self, event: Event) -> str:
        """Build the analysis prompt."""
        return f"""Analyze this infrastructure alert:

**Source:** {event.source.value}
**Severity:** {event.severity.value}
**Title:** {event.title}
**Description:** {event.description}
**Labels:** {json.dumps(event.labels, indent=2)}
**Timestamp:** {event.timestamp.isoformat()}

Provide your analysis as a JSON object."""

    def _parse_response(self, text: str) -> dict:
        """Parse the AI response."""
        # Extract JSON from response
        try:
            # Try to find JSON block in response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

        return {"summary": text, "suggested_actions": ["escalate"], "confidence": 0.3}
