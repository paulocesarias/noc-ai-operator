"""RAG knowledge base for runbooks and documentation with vector search."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.core.config import settings

logger = structlog.get_logger()

# Optional imports for vector database support
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("chromadb not installed, using in-memory search only")

try:
    from sentence_transformers import SentenceTransformer

    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    logger.warning("sentence-transformers not installed, using pattern matching only")


@dataclass
class RunbookEntry:
    """A runbook entry in the knowledge base."""

    id: str
    title: str
    alert_patterns: list[str]
    content: str
    remediation_steps: list[str]
    tags: list[str]
    severity_hints: list[str] = field(default_factory=list)
    auto_remediate: bool = False
    confidence_threshold: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> str:
        """Convert runbook to searchable document text."""
        parts = [
            f"Title: {self.title}",
            f"Alert Patterns: {', '.join(self.alert_patterns)}",
            f"Content: {self.content}",
            f"Remediation Steps: {'; '.join(self.remediation_steps)}",
            f"Tags: {', '.join(self.tags)}",
        ]
        if self.severity_hints:
            parts.append(f"Severity Hints: {', '.join(self.severity_hints)}")
        return "\n".join(parts)

    def content_hash(self) -> str:
        """Generate hash of runbook content for change detection."""
        content = json.dumps(asdict(self), sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()


@dataclass
class SearchResult:
    """Result from knowledge base search."""

    runbook: RunbookEntry
    score: float
    match_type: str  # "semantic", "pattern", "tag"


class VectorKnowledgeBase:
    """Knowledge base with vector search capabilities using ChromaDB."""

    def __init__(
        self,
        persist_directory: str | None = None,
        collection_name: str = "runbooks",
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model
        self._runbooks: dict[str, RunbookEntry] = {}
        self._pattern_index: dict[str, list[str]] = {}
        self._tag_index: dict[str, list[str]] = {}
        self._embedder: Any = None
        self._collection: Any = None
        self._client: Any = None

        # Initialize vector store if available
        if CHROMADB_AVAILABLE:
            try:
                if persist_directory:
                    self._client = chromadb.PersistentClient(
                        path=persist_directory,
                        settings=ChromaSettings(anonymized_telemetry=False),
                    )
                else:
                    self._client = chromadb.Client(
                        settings=ChromaSettings(anonymized_telemetry=False)
                    )
                self._collection = self._client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(
                    "ChromaDB initialized",
                    persist_directory=persist_directory,
                    collection=collection_name,
                )
            except Exception as e:
                logger.error("Failed to initialize ChromaDB", error=str(e))
                self._client = None
                self._collection = None

        # Initialize embedding model if available
        if EMBEDDINGS_AVAILABLE:
            try:
                self._embedder = SentenceTransformer(embedding_model)
                logger.info("Embedding model loaded", model=embedding_model)
            except Exception as e:
                logger.error("Failed to load embedding model", error=str(e))
                self._embedder = None

    def add_runbook(self, runbook: RunbookEntry) -> None:
        """Add a runbook to the knowledge base."""
        self._runbooks[runbook.id] = runbook

        # Index by alert patterns
        for pattern in runbook.alert_patterns:
            pattern_lower = pattern.lower()
            if pattern_lower not in self._pattern_index:
                self._pattern_index[pattern_lower] = []
            if runbook.id not in self._pattern_index[pattern_lower]:
                self._pattern_index[pattern_lower].append(runbook.id)

        # Index by tags
        for tag in runbook.tags:
            tag_lower = tag.lower()
            if tag_lower not in self._tag_index:
                self._tag_index[tag_lower] = []
            if runbook.id not in self._tag_index[tag_lower]:
                self._tag_index[tag_lower].append(runbook.id)

        # Add to vector store if available
        if self._collection is not None and self._embedder is not None:
            try:
                document = runbook.to_document()
                embedding = self._embedder.encode(document).tolist()
                self._collection.upsert(
                    ids=[runbook.id],
                    documents=[document],
                    embeddings=[embedding],
                    metadatas=[
                        {
                            "title": runbook.title,
                            "tags": ",".join(runbook.tags),
                            "auto_remediate": runbook.auto_remediate,
                            "content_hash": runbook.content_hash(),
                        }
                    ],
                )
            except Exception as e:
                logger.error(
                    "Failed to add runbook to vector store",
                    runbook_id=runbook.id,
                    error=str(e),
                )

        logger.info("Added runbook", id=runbook.id, title=runbook.title)

    def remove_runbook(self, runbook_id: str) -> bool:
        """Remove a runbook from the knowledge base."""
        if runbook_id not in self._runbooks:
            return False

        runbook = self._runbooks.pop(runbook_id)

        # Remove from pattern index
        for pattern in runbook.alert_patterns:
            pattern_lower = pattern.lower()
            if pattern_lower in self._pattern_index:
                self._pattern_index[pattern_lower] = [
                    rid
                    for rid in self._pattern_index[pattern_lower]
                    if rid != runbook_id
                ]

        # Remove from tag index
        for tag in runbook.tags:
            tag_lower = tag.lower()
            if tag_lower in self._tag_index:
                self._tag_index[tag_lower] = [
                    rid for rid in self._tag_index[tag_lower] if rid != runbook_id
                ]

        # Remove from vector store
        if self._collection is not None:
            try:
                self._collection.delete(ids=[runbook_id])
            except Exception as e:
                logger.error(
                    "Failed to remove runbook from vector store",
                    runbook_id=runbook_id,
                    error=str(e),
                )

        logger.info("Removed runbook", id=runbook_id)
        return True

    def semantic_search(
        self, query: str, top_k: int = 5, min_score: float = 0.3
    ) -> list[SearchResult]:
        """Search runbooks using semantic similarity."""
        if self._collection is None or self._embedder is None:
            logger.debug("Vector search unavailable, falling back to pattern search")
            return []

        try:
            query_embedding = self._embedder.encode(query).tolist()
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, len(self._runbooks)),
                include=["distances", "metadatas"],
            )

            search_results = []
            if results["ids"] and results["distances"]:
                for i, runbook_id in enumerate(results["ids"][0]):
                    # ChromaDB returns cosine distance, convert to similarity
                    distance = results["distances"][0][i]
                    score = 1 - distance  # cosine similarity

                    if score >= min_score and runbook_id in self._runbooks:
                        search_results.append(
                            SearchResult(
                                runbook=self._runbooks[runbook_id],
                                score=score,
                                match_type="semantic",
                            )
                        )

            return search_results
        except Exception as e:
            logger.error("Semantic search failed", error=str(e))
            return []

    def pattern_search(self, alert_text: str) -> list[SearchResult]:
        """Search runbooks using pattern matching."""
        alert_lower = alert_text.lower()
        matching_ids: dict[str, float] = {}

        for pattern, runbook_ids in self._pattern_index.items():
            if pattern in alert_lower:
                # Score based on pattern length relative to alert
                score = len(pattern) / len(alert_lower)
                for rid in runbook_ids:
                    if rid not in matching_ids or matching_ids[rid] < score:
                        matching_ids[rid] = score

        return [
            SearchResult(
                runbook=self._runbooks[rid],
                score=score,
                match_type="pattern",
            )
            for rid, score in sorted(
                matching_ids.items(), key=lambda x: x[1], reverse=True
            )
            if rid in self._runbooks
        ]

    def tag_search(self, tags: list[str]) -> list[SearchResult]:
        """Search runbooks by tags."""
        matching_ids: dict[str, int] = {}

        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower in self._tag_index:
                for rid in self._tag_index[tag_lower]:
                    matching_ids[rid] = matching_ids.get(rid, 0) + 1

        return [
            SearchResult(
                runbook=self._runbooks[rid],
                score=count / len(tags),
                match_type="tag",
            )
            for rid, count in sorted(
                matching_ids.items(), key=lambda x: x[1], reverse=True
            )
            if rid in self._runbooks
        ]

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        use_semantic: bool = True,
    ) -> list[SearchResult]:
        """Combined search using multiple strategies."""
        all_results: dict[str, SearchResult] = {}

        # Semantic search (highest priority if available)
        if use_semantic:
            semantic_results = self.semantic_search(query, top_k=top_k)
            for result in semantic_results:
                key = result.runbook.id
                if key not in all_results or result.score > all_results[key].score:
                    all_results[key] = result

        # Pattern search
        pattern_results = self.pattern_search(query)
        for result in pattern_results:
            key = result.runbook.id
            # Boost pattern matches slightly if also found semantically
            if key in all_results:
                all_results[key].score = min(1.0, all_results[key].score + 0.1)
            else:
                all_results[key] = result

        # Tag search
        if tags:
            tag_results = self.tag_search(tags)
            for result in tag_results:
                key = result.runbook.id
                if key in all_results:
                    all_results[key].score = min(1.0, all_results[key].score + 0.05)
                else:
                    all_results[key] = result

        # Sort by score and return top_k
        sorted_results = sorted(
            all_results.values(), key=lambda x: x.score, reverse=True
        )
        return sorted_results[:top_k]

    def find_by_alert(self, alert_title: str, alert_description: str = "") -> list[RunbookEntry]:
        """Find runbooks matching an alert (backward compatible)."""
        query = f"{alert_title} {alert_description}".strip()
        results = self.search(query, top_k=5)
        return [r.runbook for r in results]

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
            if rb.auto_remediate:
                parts.append(f"\n*Auto-remediation available with confidence threshold: {rb.confidence_threshold}*\n")
            parts.append("\n")

        return "".join(parts)

    def format_search_results(self, results: list[SearchResult]) -> str:
        """Format search results for LLM context with scores."""
        if not results:
            return ""

        parts = ["## Relevant Runbooks (by relevance)\n"]
        for i, result in enumerate(results, 1):
            rb = result.runbook
            parts.append(f"### {i}. {rb.title} (score: {result.score:.2f}, match: {result.match_type})\n")
            parts.append(rb.content + "\n")
            if rb.remediation_steps:
                parts.append("**Remediation Steps:**\n")
                for j, step in enumerate(rb.remediation_steps, 1):
                    parts.append(f"{j}. {step}\n")
            if rb.auto_remediate:
                parts.append(f"\n*Auto-remediation enabled (threshold: {rb.confidence_threshold})*\n")
            parts.append("\n")

        return "".join(parts)

    def import_from_file(self, file_path: str) -> int:
        """Import runbooks from a JSON file."""
        path = Path(file_path)
        if not path.exists():
            logger.error("Runbook file not found", path=file_path)
            return 0

        try:
            with open(path) as f:
                data = json.load(f)

            count = 0
            runbooks = data if isinstance(data, list) else data.get("runbooks", [])
            for item in runbooks:
                runbook = RunbookEntry(
                    id=item["id"],
                    title=item["title"],
                    alert_patterns=item.get("alert_patterns", []),
                    content=item.get("content", ""),
                    remediation_steps=item.get("remediation_steps", []),
                    tags=item.get("tags", []),
                    severity_hints=item.get("severity_hints", []),
                    auto_remediate=item.get("auto_remediate", False),
                    confidence_threshold=item.get("confidence_threshold", 0.7),
                    metadata=item.get("metadata", {}),
                )
                self.add_runbook(runbook)
                count += 1

            logger.info("Imported runbooks from file", path=file_path, count=count)
            return count
        except Exception as e:
            logger.error("Failed to import runbooks", path=file_path, error=str(e))
            return 0

    def export_to_file(self, file_path: str) -> bool:
        """Export runbooks to a JSON file."""
        try:
            data = {"runbooks": [asdict(rb) for rb in self._runbooks.values()]}
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("Exported runbooks to file", path=file_path, count=len(self._runbooks))
            return True
        except Exception as e:
            logger.error("Failed to export runbooks", path=file_path, error=str(e))
            return False


# Backward-compatible alias
KnowledgeBase = VectorKnowledgeBase


# Default runbooks for common scenarios
DEFAULT_RUNBOOKS = [
    RunbookEntry(
        id="k8s-crashloop",
        title="Kubernetes Pod CrashLoopBackOff",
        alert_patterns=["crashloopbackoff", "crash loop", "pod crash", "container crash"],
        content="Pod is repeatedly crashing and being restarted by Kubernetes. This often indicates an application error, misconfiguration, or resource constraint.",
        remediation_steps=[
            "Check pod logs: kubectl logs <pod> --previous",
            "Check pod events: kubectl describe pod <pod>",
            "If recent deployment, consider rollback: kubectl rollout undo deployment/<name>",
            "If resource issue, check memory/CPU limits",
            "Restart pod if transient issue suspected: kubectl delete pod <pod>",
        ],
        tags=["kubernetes", "pod", "crash", "container"],
        severity_hints=["critical", "warning"],
        auto_remediate=True,
        confidence_threshold=0.8,
    ),
    RunbookEntry(
        id="k8s-oom",
        title="Kubernetes Out of Memory",
        alert_patterns=["oomkilled", "out of memory", "memory limit", "oom killed", "oom"],
        content="Container was killed due to exceeding memory limits. This can cause service disruption and potential data loss.",
        remediation_steps=[
            "Check current memory usage: kubectl top pods",
            "Review application for memory leaks",
            "Analyze heap dumps if available",
            "Consider increasing memory limits if justified",
            "Scale horizontally if single pod limit reached",
        ],
        tags=["kubernetes", "memory", "oom", "resource"],
        severity_hints=["critical"],
        auto_remediate=False,
        confidence_threshold=0.7,
    ),
    RunbookEntry(
        id="high-cpu",
        title="High CPU Usage",
        alert_patterns=["high cpu", "cpu usage", "cpu threshold", "cpu spike", "cpu utilization"],
        content="System or container CPU usage exceeds threshold. This may indicate a performance issue, inefficient code, or legitimate high load.",
        remediation_steps=[
            "Identify top CPU consuming processes: top or kubectl top pods",
            "Check for runaway processes or infinite loops",
            "Profile application if available",
            "Consider scaling if legitimate load: kubectl scale deployment/<name> --replicas=N",
            "Review recent deployments for performance regression",
        ],
        tags=["cpu", "performance", "scaling"],
        severity_hints=["warning"],
        auto_remediate=True,
        confidence_threshold=0.75,
    ),
    RunbookEntry(
        id="disk-full",
        title="Disk Space Critical",
        alert_patterns=["disk full", "disk space", "filesystem full", "no space", "volume full", "storage full"],
        content="Disk space is critically low on the system. This can cause application failures, data corruption, and service outages.",
        remediation_steps=[
            "Identify large files: du -sh /* | sort -h",
            "Check and rotate logs: journalctl --vacuum-size=500M",
            "Clean up old Docker images: docker system prune -a",
            "Check for disk space leaks (deleted but open files): lsof +L1",
            "Clear package cache: apt clean or yum clean all",
            "Expand disk if cleanup insufficient",
        ],
        tags=["disk", "storage", "cleanup"],
        severity_hints=["critical"],
        auto_remediate=False,
        confidence_threshold=0.7,
    ),
    RunbookEntry(
        id="service-down",
        title="Service Unavailable",
        alert_patterns=["service down", "unavailable", "connection refused", "unhealthy", "endpoint down", "health check failed"],
        content="Service is not responding to health checks. This typically indicates the service has crashed, is overloaded, or has connectivity issues.",
        remediation_steps=[
            "Check service status and logs",
            "Verify network connectivity: curl or telnet to endpoint",
            "Check dependent services (database, cache, queue)",
            "Restart service if unresponsive: kubectl rollout restart deployment/<name>",
            "Check for resource exhaustion (CPU, memory, connections)",
        ],
        tags=["service", "availability", "health"],
        severity_hints=["critical"],
        auto_remediate=True,
        confidence_threshold=0.8,
    ),
    RunbookEntry(
        id="database-connection",
        title="Database Connection Issues",
        alert_patterns=["database connection", "db connection", "connection pool", "too many connections", "database unavailable"],
        content="Application is having trouble connecting to the database. This could be due to connection pool exhaustion, network issues, or database overload.",
        remediation_steps=[
            "Check database server health and availability",
            "Review connection pool settings and current usage",
            "Check for long-running queries: SHOW PROCESSLIST",
            "Verify network connectivity to database",
            "Consider scaling database or connection pool",
            "Kill idle connections if pool exhausted",
        ],
        tags=["database", "connection", "pool"],
        severity_hints=["critical", "warning"],
        auto_remediate=False,
        confidence_threshold=0.7,
    ),
    RunbookEntry(
        id="ssl-certificate",
        title="SSL Certificate Expiring",
        alert_patterns=["ssl expir", "certificate expir", "tls expir", "cert expir", "ssl warning"],
        content="SSL/TLS certificate is expiring soon. Certificate expiration will cause service disruption and security warnings.",
        remediation_steps=[
            "Check certificate expiration: openssl s_client -connect host:443",
            "Renew certificate via cert-manager or manual process",
            "Verify DNS and domain ownership",
            "Update certificate in ingress/load balancer",
            "Test certificate after renewal",
        ],
        tags=["ssl", "tls", "certificate", "security"],
        severity_hints=["warning"],
        auto_remediate=False,
        confidence_threshold=0.9,
    ),
    RunbookEntry(
        id="network-latency",
        title="High Network Latency",
        alert_patterns=["high latency", "network latency", "slow response", "response time", "timeout"],
        content="Network latency is elevated, causing slow response times and potential timeouts.",
        remediation_steps=[
            "Identify latency source with traceroute/mtr",
            "Check network interface errors: ip -s link",
            "Review DNS resolution times",
            "Check for packet loss",
            "Verify no network congestion or bandwidth issues",
            "Consider CDN or edge caching if external",
        ],
        tags=["network", "latency", "performance"],
        severity_hints=["warning"],
        auto_remediate=False,
        confidence_threshold=0.6,
    ),
    RunbookEntry(
        id="snmp-interface-down",
        title="Network Interface Down (SNMP)",
        alert_patterns=["interface down", "link down", "port down", "snmp interface"],
        content="Network interface is operationally down while administratively enabled. This indicates a physical or protocol-level issue.",
        remediation_steps=[
            "Check physical connectivity (cable, SFP)",
            "Verify interface configuration",
            "Check for interface errors via SNMP polling",
            "Review switch port status on remote end",
            "Bounce interface if transient issue: shut/no shut",
        ],
        tags=["snmp", "network", "interface", "legacy"],
        severity_hints=["critical"],
        auto_remediate=False,
        confidence_threshold=0.7,
    ),
    RunbookEntry(
        id="storage-array-alert",
        title="Storage Array Alert",
        alert_patterns=["storage array", "san alert", "disk array", "raid degraded", "drive failure"],
        content="Storage array is reporting an alert. This could indicate drive failure, RAID degradation, or controller issues.",
        remediation_steps=[
            "Check array management interface for details",
            "Identify failed or degraded components",
            "Verify hot spare availability and activation",
            "Schedule drive replacement if failed",
            "Check array replication status if configured",
            "Contact storage vendor support if critical",
        ],
        tags=["storage", "san", "raid", "legacy"],
        severity_hints=["critical", "warning"],
        auto_remediate=False,
        confidence_threshold=0.6,
    ),
]


def create_default_knowledge_base(
    persist_directory: str | None = None,
) -> VectorKnowledgeBase:
    """Create a knowledge base with default runbooks."""
    kb = VectorKnowledgeBase(persist_directory=persist_directory)
    for runbook in DEFAULT_RUNBOOKS:
        kb.add_runbook(runbook)
    return kb


# Singleton instance
_knowledge_base: VectorKnowledgeBase | None = None


def get_knowledge_base() -> VectorKnowledgeBase:
    """Get or create the global knowledge base instance."""
    global _knowledge_base
    if _knowledge_base is None:
        persist_dir = getattr(settings, "knowledge_base_path", None)
        _knowledge_base = create_default_knowledge_base(persist_directory=persist_dir)
    return _knowledge_base
