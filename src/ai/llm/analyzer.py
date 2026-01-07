"""Alert analyzer using Claude API."""

import asyncio
import json
from functools import partial

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
- k8s_restart_pod: Restart a Kubernetes pod (safe for stateless apps)
- k8s_scale_deployment: Scale a deployment up/down
- k8s_rollback: Rollback to previous deployment version (requires approval)
- ansible_playbook: Run an Ansible playbook
- ssh_command: Execute SSH command on target host
- snmp_set: Set SNMP OID value on network device
- escalate: Escalate to human operator
- no_action: No action needed (informational alert)

For Kubernetes alerts:
- Pod CrashLoopBackOff -> k8s_restart_pod or k8s_rollback
- High memory/CPU -> k8s_scale_deployment
- Deployment failed -> k8s_rollback
- Service unavailable -> check pods first

For network/infrastructure alerts:
- Device unreachable -> escalate (needs investigation)
- High CPU on network device -> snmp_set to adjust thresholds or escalate
- Storage full -> ssh_command to clean logs or escalate

Respond with a JSON object containing:
{
    "summary": "Brief summary of the issue",
    "root_cause": "Likely root cause if determinable",
    "suggested_actions": ["action_type1", "action_type2"],
    "action_parameters": {
        "action_type1": {"param": "value"},
        "action_type2": {"param": "value"}
    },
    "confidence": 0.0-1.0,
    "reasoning": "Explanation of your analysis",
    "requires_approval": true/false
}

Set requires_approval=true for:
- Destructive operations (rollback, delete)
- Actions affecting production workloads
- Confidence below 0.7
- Unfamiliar alert patterns
- SSH commands that modify system state
"""


class AlertAnalyzer:
    """Analyzes alerts using Claude API."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._runbook_context: dict[str, str] = {}

    def add_runbook(self, alert_pattern: str, runbook: str) -> None:
        """Add a runbook for context enrichment."""
        self._runbook_context[alert_pattern] = runbook

    async def analyze(self, event: Event) -> AIAnalysis:
        """Analyze an event and suggest remediation actions."""
        prompt = self._build_prompt(event)

        try:
            # Run sync API call in thread pool
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                partial(
                    self.client.messages.create,
                    model=settings.claude_model,
                    max_tokens=settings.claude_max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )

            result = self._parse_response(response.content[0].text)

            # Validate and convert actions
            valid_actions = []
            for action in result.get("suggested_actions", ["no_action"]):
                try:
                    valid_actions.append(ActionType(action))
                except ValueError:
                    logger.warning("Unknown action type from AI", action=action)
                    valid_actions.append(ActionType.ESCALATE)

            if not valid_actions:
                valid_actions = [ActionType.ESCALATE]

            confidence = result.get("confidence", 0.5)
            requires_approval = result.get("requires_approval", True)

            # Force approval for low confidence
            if confidence < 0.7:
                requires_approval = True

            return AIAnalysis(
                event_id=event.id,
                summary=result.get("summary", "Unable to generate summary"),
                root_cause=result.get("root_cause"),
                suggested_actions=valid_actions,
                confidence=confidence,
                reasoning=result.get("reasoning", ""),
                requires_approval=requires_approval,
            )

        except anthropic.APIError as e:
            logger.error("Claude API error", error=str(e))
            return self._fallback_analysis(event, f"API error: {e}")
        except Exception as e:
            logger.error("AI analysis failed", error=str(e))
            return self._fallback_analysis(event, str(e))

    def _build_prompt(self, event: Event) -> str:
        """Build the analysis prompt with optional runbook context."""
        # Check for matching runbook
        runbook_context = ""
        for pattern, runbook in self._runbook_context.items():
            if pattern.lower() in event.title.lower():
                runbook_context = f"\n\n**Relevant Runbook:**\n{runbook}\n"
                break

        return f"""Analyze this infrastructure alert:

**Source:** {event.source.value}
**Severity:** {event.severity.value}
**Title:** {event.title}
**Description:** {event.description}
**Labels:** {json.dumps(event.labels, indent=2)}
**Timestamp:** {event.timestamp.isoformat()}
{runbook_context}
Provide your analysis as a JSON object."""

    def _parse_response(self, text: str) -> dict:
        """Parse the AI response."""
        # Try to extract JSON from response
        try:
            # Look for JSON block (may be wrapped in markdown)
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                if end > start:
                    return json.loads(text[start:end].strip())

            # Try to find raw JSON
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse AI response as JSON", error=str(e))

        # Fallback
        return {
            "summary": text[:200] if len(text) > 200 else text,
            "suggested_actions": ["escalate"],
            "confidence": 0.3,
            "reasoning": "Failed to parse structured response",
            "requires_approval": True,
        }

    def _fallback_analysis(self, event: Event, error: str) -> AIAnalysis:
        """Return a safe fallback analysis on error."""
        return AIAnalysis(
            event_id=event.id,
            summary=f"Analysis failed: {error}",
            suggested_actions=[ActionType.ESCALATE],
            confidence=0.0,
            reasoning=f"AI analysis error - escalating for manual review. Error: {error}",
            requires_approval=True,
        )
