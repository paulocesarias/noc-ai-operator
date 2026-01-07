"""API routes for runbook knowledge base management."""

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field

from src.ai.rag import (
    RunbookEntry,
    SearchResult,
    get_knowledge_base,
    CHROMADB_AVAILABLE,
    EMBEDDINGS_AVAILABLE,
)

logger = structlog.get_logger()
router = APIRouter()


class RunbookCreate(BaseModel):
    """Request model for creating a runbook."""

    id: str = Field(..., description="Unique identifier for the runbook")
    title: str = Field(..., description="Title of the runbook")
    alert_patterns: list[str] = Field(
        default_factory=list, description="Alert patterns to match"
    )
    content: str = Field(..., description="Main content of the runbook")
    remediation_steps: list[str] = Field(
        default_factory=list, description="Steps to remediate the issue"
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    severity_hints: list[str] = Field(
        default_factory=list, description="Severity level hints"
    )
    auto_remediate: bool = Field(
        default=False, description="Whether auto-remediation is allowed"
    )
    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto-remediation",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class RunbookResponse(BaseModel):
    """Response model for a runbook."""

    id: str
    title: str
    alert_patterns: list[str]
    content: str
    remediation_steps: list[str]
    tags: list[str]
    severity_hints: list[str]
    auto_remediate: bool
    confidence_threshold: float
    metadata: dict[str, Any]


class SearchRequest(BaseModel):
    """Request model for searching runbooks."""

    query: str = Field(..., description="Search query")
    tags: list[str] | None = Field(None, description="Optional tags to filter by")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of results to return")
    use_semantic: bool = Field(
        default=True, description="Whether to use semantic search"
    )


class SearchResultResponse(BaseModel):
    """Response model for a search result."""

    runbook: RunbookResponse
    score: float
    match_type: str


class KnowledgeBaseStatus(BaseModel):
    """Status of the knowledge base."""

    runbook_count: int
    chromadb_available: bool
    embeddings_available: bool
    semantic_search_enabled: bool


def runbook_to_response(runbook: RunbookEntry) -> RunbookResponse:
    """Convert a RunbookEntry to a response model."""
    return RunbookResponse(
        id=runbook.id,
        title=runbook.title,
        alert_patterns=runbook.alert_patterns,
        content=runbook.content,
        remediation_steps=runbook.remediation_steps,
        tags=runbook.tags,
        severity_hints=runbook.severity_hints,
        auto_remediate=runbook.auto_remediate,
        confidence_threshold=runbook.confidence_threshold,
        metadata=runbook.metadata,
    )


def search_result_to_response(result: SearchResult) -> SearchResultResponse:
    """Convert a SearchResult to a response model."""
    return SearchResultResponse(
        runbook=runbook_to_response(result.runbook),
        score=result.score,
        match_type=result.match_type,
    )


@router.get("/status", response_model=KnowledgeBaseStatus)
async def get_status() -> KnowledgeBaseStatus:
    """Get knowledge base status."""
    kb = get_knowledge_base()
    return KnowledgeBaseStatus(
        runbook_count=len(kb.list_runbooks()),
        chromadb_available=CHROMADB_AVAILABLE,
        embeddings_available=EMBEDDINGS_AVAILABLE,
        semantic_search_enabled=kb._collection is not None and kb._embedder is not None,
    )


@router.get("/runbooks", response_model=list[RunbookResponse])
async def list_runbooks(
    tag: str | None = Query(None, description="Filter by tag"),
) -> list[RunbookResponse]:
    """List all runbooks in the knowledge base."""
    kb = get_knowledge_base()
    runbooks = kb.list_runbooks()

    if tag:
        runbooks = [rb for rb in runbooks if tag.lower() in [t.lower() for t in rb.tags]]

    return [runbook_to_response(rb) for rb in runbooks]


@router.get("/runbooks/{runbook_id}", response_model=RunbookResponse)
async def get_runbook(runbook_id: str) -> RunbookResponse:
    """Get a specific runbook by ID."""
    kb = get_knowledge_base()
    runbook = kb.get_runbook(runbook_id)

    if not runbook:
        raise HTTPException(status_code=404, detail="Runbook not found")

    return runbook_to_response(runbook)


@router.post("/runbooks", response_model=RunbookResponse, status_code=201)
async def create_runbook(runbook: RunbookCreate) -> RunbookResponse:
    """Create a new runbook."""
    kb = get_knowledge_base()

    # Check if runbook already exists
    if kb.get_runbook(runbook.id):
        raise HTTPException(
            status_code=409, detail=f"Runbook with ID '{runbook.id}' already exists"
        )

    entry = RunbookEntry(
        id=runbook.id,
        title=runbook.title,
        alert_patterns=runbook.alert_patterns,
        content=runbook.content,
        remediation_steps=runbook.remediation_steps,
        tags=runbook.tags,
        severity_hints=runbook.severity_hints,
        auto_remediate=runbook.auto_remediate,
        confidence_threshold=runbook.confidence_threshold,
        metadata=runbook.metadata,
    )
    kb.add_runbook(entry)

    logger.info("Runbook created via API", runbook_id=runbook.id)
    return runbook_to_response(entry)


@router.put("/runbooks/{runbook_id}", response_model=RunbookResponse)
async def update_runbook(runbook_id: str, runbook: RunbookCreate) -> RunbookResponse:
    """Update an existing runbook."""
    kb = get_knowledge_base()

    # Check if runbook exists
    if not kb.get_runbook(runbook_id):
        raise HTTPException(status_code=404, detail="Runbook not found")

    # Remove old and add new
    kb.remove_runbook(runbook_id)

    entry = RunbookEntry(
        id=runbook_id,  # Use path ID, not body ID
        title=runbook.title,
        alert_patterns=runbook.alert_patterns,
        content=runbook.content,
        remediation_steps=runbook.remediation_steps,
        tags=runbook.tags,
        severity_hints=runbook.severity_hints,
        auto_remediate=runbook.auto_remediate,
        confidence_threshold=runbook.confidence_threshold,
        metadata=runbook.metadata,
    )
    kb.add_runbook(entry)

    logger.info("Runbook updated via API", runbook_id=runbook_id)
    return runbook_to_response(entry)


@router.delete("/runbooks/{runbook_id}", status_code=204)
async def delete_runbook(runbook_id: str) -> None:
    """Delete a runbook."""
    kb = get_knowledge_base()

    if not kb.remove_runbook(runbook_id):
        raise HTTPException(status_code=404, detail="Runbook not found")

    logger.info("Runbook deleted via API", runbook_id=runbook_id)


@router.post("/search", response_model=list[SearchResultResponse])
async def search_runbooks(request: SearchRequest) -> list[SearchResultResponse]:
    """Search runbooks using semantic and pattern matching."""
    kb = get_knowledge_base()
    results = kb.search(
        query=request.query,
        tags=request.tags,
        top_k=request.top_k,
        use_semantic=request.use_semantic,
    )
    return [search_result_to_response(r) for r in results]


@router.get("/search", response_model=list[SearchResultResponse])
async def search_runbooks_get(
    q: str = Query(..., description="Search query"),
    tags: str | None = Query(None, description="Comma-separated tags to filter by"),
    top_k: int = Query(5, ge=1, le=20, description="Number of results"),
    semantic: bool = Query(True, description="Use semantic search"),
) -> list[SearchResultResponse]:
    """Search runbooks (GET method for convenience)."""
    kb = get_knowledge_base()
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    results = kb.search(
        query=q,
        tags=tag_list,
        top_k=top_k,
        use_semantic=semantic,
    )
    return [search_result_to_response(r) for r in results]


@router.get("/tags", response_model=list[str])
async def list_tags() -> list[str]:
    """List all unique tags in the knowledge base."""
    kb = get_knowledge_base()
    tags: set[str] = set()
    for runbook in kb.list_runbooks():
        tags.update(runbook.tags)
    return sorted(tags)


@router.post("/import", response_model=dict[str, int])
async def import_runbooks(file: UploadFile = File(...)) -> dict[str, int]:
    """Import runbooks from a JSON file."""
    import json
    import tempfile
    from pathlib import Path

    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON file")

    try:
        content = await file.read()
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    kb = get_knowledge_base()
    count = 0
    errors = 0

    runbooks = data if isinstance(data, list) else data.get("runbooks", [])
    for item in runbooks:
        try:
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
            kb.add_runbook(runbook)
            count += 1
        except Exception as e:
            logger.error("Failed to import runbook", error=str(e), item=item.get("id", "unknown"))
            errors += 1

    logger.info("Runbooks imported via API", count=count, errors=errors)
    return {"imported": count, "errors": errors}


@router.get("/export")
async def export_runbooks() -> dict[str, Any]:
    """Export all runbooks as JSON."""
    from dataclasses import asdict

    kb = get_knowledge_base()
    runbooks = [asdict(rb) for rb in kb.list_runbooks()]
    return {"runbooks": runbooks, "count": len(runbooks)}
