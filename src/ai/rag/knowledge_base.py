"""RAG knowledge base for runbooks and documentation."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class RunbookEntry:
    """A runbook entry in the knowledge base."""

    id: str
    title: str
    alert_patterns: list[str]
    content: str
    remediation_steps: list[str]
    tags: list[str]


class KnowledgeBase:
    """Knowledge base for runbooks and operational documentation.

    This is a simple in-memory implementation. For production, integrate
    with a vector database like pgvector for semantic search.
    """

    def __init__(self) -> None:
        self._runbooks: dict[str, RunbookEntry] = {}
        self._pattern_index: dict[str, list[str]] = {}

    def add_runbook(self, runbook: RunbookEntry) -> None:
        """Add a runbook to the knowledge base."""
        self._runbooks[runbook.id] = runbook

        # Index by alert patterns
        for pattern in runbook.alert_patterns:
            pattern_lower = pattern.lower()
            if pattern_lower not in self._pattern_index:
                self._pattern_index[pattern_lower] = []
            self._pattern_index[pattern_lower].append(runbook.id)

        logger.info("Added runbook", id=runbook.id, title=runbook.title)

    def find_by_alert(self, alert_title: str) -> list[RunbookEntry]:
        """Find runbooks matching an alert title."""
        alert_lower = alert_title.lower()
        matching_ids: set[str] = set()

        for pattern, runbook_ids in self._pattern_index.items():
            if pattern in alert_lower:
                matching_ids.update(runbook_ids)

        return [self._runbooks[rid] for rid in matching_ids if rid in self._runbooks]

    def get_runbook(self, runbook_id: str) -> RunbookEntry | None:
        """Get a runbook by ID."""
        return self._runbooks.get(runbook_id)

    def list_runbooks(self) -> list[RunbookEntry]:
        """List all runbooks."""
        return list(self._runbooks.values())

    def format_for_context(self, runbooks: list[RunbookEntry]) -> str:
        """Format runbooks for LLM context."""
        if not runbooks:
            return ""

        parts = ["## Relevant Runbooks\n"]
        for rb in runbooks:
            parts.append(f"### {rb.title}\n")
            parts.append(rb.content + "\n")
            if rb.remediation_steps:
                parts.append("**Remediation Steps:**\n")
                for i, step in enumerate(rb.remediation_steps, 1):
                    parts.append(f"{i}. {step}\n")
            parts.append("\n")

        return "".join(parts)


# Default runbooks for common scenarios
DEFAULT_RUNBOOKS = [
    RunbookEntry(
        id="k8s-crashloop",
        title="Kubernetes Pod CrashLoopBackOff",
        alert_patterns=["crashloopbackoff", "crash loop", "pod crash"],
        content="Pod is repeatedly crashing and being restarted by Kubernetes.",
        remediation_steps=[
            "Check pod logs: kubectl logs <pod> --previous",
            "Check pod events: kubectl describe pod <pod>",
            "If recent deployment, consider rollback",
            "If resource issue, check memory/CPU limits",
            "Restart pod if transient issue suspected",
        ],
        tags=["kubernetes", "pod", "crash"],
    ),
    RunbookEntry(
        id="k8s-oom",
        title="Kubernetes Out of Memory",
        alert_patterns=["oomkilled", "out of memory", "memory limit"],
        content="Container was killed due to exceeding memory limits.",
        remediation_steps=[
            "Check current memory usage and limits",
            "Review application for memory leaks",
            "Consider increasing memory limits if justified",
            "Scale horizontally if single pod limit reached",
        ],
        tags=["kubernetes", "memory", "oom"],
    ),
    RunbookEntry(
        id="high-cpu",
        title="High CPU Usage",
        alert_patterns=["high cpu", "cpu usage", "cpu threshold"],
        content="System or container CPU usage exceeds threshold.",
        remediation_steps=[
            "Identify top CPU consuming processes",
            "Check for runaway processes or infinite loops",
            "Consider scaling if legitimate load",
            "Review recent deployments for performance regression",
        ],
        tags=["cpu", "performance"],
    ),
    RunbookEntry(
        id="disk-full",
        title="Disk Space Critical",
        alert_patterns=["disk full", "disk space", "filesystem full", "no space"],
        content="Disk space is critically low on the system.",
        remediation_steps=[
            "Identify large files: du -sh /* | sort -h",
            "Check and rotate logs",
            "Clean up old Docker images: docker system prune",
            "Check for disk space leaks (deleted but open files)",
            "Expand disk if cleanup insufficient",
        ],
        tags=["disk", "storage"],
    ),
    RunbookEntry(
        id="service-down",
        title="Service Unavailable",
        alert_patterns=["service down", "unavailable", "connection refused", "unhealthy"],
        content="Service is not responding to health checks.",
        remediation_steps=[
            "Check service status and logs",
            "Verify network connectivity",
            "Check dependent services (database, cache)",
            "Restart service if unresponsive",
            "Check for resource exhaustion",
        ],
        tags=["service", "availability"],
    ),
]


def create_default_knowledge_base() -> KnowledgeBase:
    """Create a knowledge base with default runbooks."""
    kb = KnowledgeBase()
    for runbook in DEFAULT_RUNBOOKS:
        kb.add_runbook(runbook)
    return kb
