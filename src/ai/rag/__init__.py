"""RAG (Retrieval Augmented Generation) for runbook knowledge base."""

from src.ai.rag.knowledge_base import (
    CHROMADB_AVAILABLE,
    DEFAULT_RUNBOOKS,
    EMBEDDINGS_AVAILABLE,
    KnowledgeBase,
    RunbookEntry,
    SearchResult,
    VectorKnowledgeBase,
    create_default_knowledge_base,
    get_knowledge_base,
)

__all__ = [
    "KnowledgeBase",
    "VectorKnowledgeBase",
    "RunbookEntry",
    "SearchResult",
    "DEFAULT_RUNBOOKS",
    "CHROMADB_AVAILABLE",
    "EMBEDDINGS_AVAILABLE",
    "create_default_knowledge_base",
    "get_knowledge_base",
]
