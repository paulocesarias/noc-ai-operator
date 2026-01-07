"""Alert analyzer using Claude API with RAG-enhanced context."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import anthropic
import structlog

from src.ai.rag import RunbookEntry, get_knowledge_base
from src.core.config import settings
from src.core.models import ActionType, AIAnalysis, Event

logger = structlog.get_logger()

# Thread pool for blocking operations
_executor = ThreadPoolExecutor(max_workers=5)

SYSTEM_PROMPT = """You are an expert NOC (Network Operations Center) operator AI.
Your job is to analyze infrastructure alerts and suggest remediation actions.

When analyzing an alert, consider:
1. The severity and potential impact
2. Common root causes for this type of issue
3. Safe remediation actions that can be automated
4. Whether human approval is needed for risky operations
5. The relevant runbook context provided (if any)

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
- Interface down -> check physical connectivity, escalate

When runbook context is provided:
- Follow the remediation steps from the runbook
- Use the suggested confidence threshold from the runbook
- Note if auto-remediation is enabled for this type of issue

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
    "requires_approval": true/false,
    "runbook_id": "id of matched runbook if any"
}

Set requires_approval=true for:
- Destructive operations (rollback, delete)
- Actions affecting production workloads
- Confidence below 0.7 (or runbook's confidence threshold)
- Unfamiliar alert patterns without runbook guidance
- SSH commands that modify system state
"""


class AlertAnalyzer:
    """Analyzes alerts using Claude API with RAG-enhanced context."""

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._runbook_context: dict[str, str] = {}  # Legacy support

    def add_runbook(self, alert_pattern: str, runbook: str) -> None:
        """Add a runbook for context enrichment (legacy method)."""
        self._runbook_context[alert_pattern] = runbook

    async def analyze(self, event: Event) -> AIAnalysis:
        """Analyze an event and suggest remediation actions."""
        # Get knowledge base context
        kb = get_knowledge_base()
        query = f"{event.title} {event.description}"
        search_results = kb.search(query, tags=list(event.labels.keys()), top_k=3)
        runbook_context = kb.format_search_results(search_results)

        # Build prompt with RAG context
        prompt = self._build_prompt(event, runbook_context)

        # Determine confidence threshold from top matching runbook
        confidence_threshold = 0.7
        auto_remediate_allowed = False
        matched_runbook: RunbookEntry | None = None
        if search_results:
            top_result = search_results[0]
            if top_result.score >= 0.5:  # Only use runbook if good match
                matched_runbook = top_result.runbook
                confidence_threshold = matched_runbook.confidence_threshold
                auto_remediate_allowed = matched_runbook.auto_remediate

        try:
            # Run sync API call in thread pool
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                _executor,
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

            # Apply runbook-specific rules
            if matched_runbook:
                # Use runbook's confidence threshold
                if confidence < confidence_threshold:
                    requires_approval = True
                # Only skip approval if runbook allows auto-remediation
                elif auto_remediate_allowed and confidence >= confidence_threshold:
                    requires_approval = False
            else:
                # Default: require approval for low confidence
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
                runbook_id=result.get("runbook_id") or (matched_runbook.id if matched_runbook else None),
            )

        except anthropic.APIError as e:
            logger.error("Claude API error", error=str(e))
            return self._fallback_analysis(event, f"API error: {e}")
        except Exception as e:
            logger.error("AI analysis failed", error=str(e))
            return self._fallback_analysis(event, str(e))

    def _build_prompt(self, event: Event, runbook_context: str = "") -> str:
        """Build the analysis prompt with RAG-enhanced runbook context."""
        # Legacy runbook context fallback
        legacy_context = ""
        if not runbook_context:
            for pattern, runbook in self._runbook_context.items():
                if pattern.lower() in event.title.lower():
                    legacy_context = f"\n\n**Relevant Runbook (Legacy):**\n{runbook}\n"
                    break

        context_section = runbook_context or legacy_context

        return f"""Analyze this infrastructure alert:

**Source:** {event.source.value}
**Severity:** {event.severity.value}
**Title:** {event.title}
**Description:** {event.description}
**Labels:** {json.dumps(event.labels, indent=2)}
**Timestamp:** {event.timestamp.isoformat()}
{context_section}
Provide your analysis as a JSON object."""

    def _parse_response(self, text: str) -> dict[str, Any]:
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


class BatchAnalyzer:
    """Batch analyzer for processing multiple events efficiently."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self.analyzer = AlertAnalyzer()
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def analyze_batch(self, events: list[Event]) -> list[AIAnalysis]:
        """Analyze multiple events concurrently."""
        async def analyze_with_limit(event: Event) -> AIAnalysis:
            async with self.semaphore:
                return await self.analyzer.analyze(event)

        tasks = [analyze_with_limit(event) for event in events]
        return await asyncio.gather(*tasks)


# Singleton instance
_analyzer: AlertAnalyzer | None = None


def get_analyzer() -> AlertAnalyzer:
    """Get or create the global analyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = AlertAnalyzer()
    return _analyzer
