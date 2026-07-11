import os
import time
from langchain_qdrant import QdrantVectorStore
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.tools import tool

_store: QdrantVectorStore | None = None

_sources_cache: list[str] | None = None
_sources_cache_at: float = 0
SOURCES_CACHE_TTL = 300


def _get_store() -> QdrantVectorStore:
    global _store
    if _store is None:
        embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
        _store = QdrantVectorStore.from_existing_collection(
            embedding=embeddings,
            collection_name="nexus_docs",
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY"),
        )
    return _store


@tool
def rag_search(query: str) -> str:
    """Search internal company documents — HR policies, IT support guides, and company FAQs."""
    try:
        docs = _get_store().similarity_search(query, k=3)
        if not docs:
            return "No relevant documents found."
        return "\n\n".join(
            f"[{doc.metadata.get('source', 'doc')}]: {doc.page_content}"
            for doc in docs
        )
    except Exception as e:
        return f"Document search unavailable: {e}"


def search_with_scores(query: str, k: int = 3) -> list[dict]:
    results = _get_store().similarity_search_with_score(query, k=k)
    return [
        {
            "source": doc.metadata.get("source", "doc"),
            "content": doc.page_content,
            "score": round(float(score), 4),
        }
        for doc, score in results
    ]


def ping() -> dict:
    try:
        _get_store().client.get_collections()
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def list_sources() -> list[str]:
    global _sources_cache, _sources_cache_at
    if _sources_cache is not None and (time.monotonic() - _sources_cache_at) < SOURCES_CACHE_TTL:
        return _sources_cache

    client = _get_store().client
    seen = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name="nexus_docs",
            with_payload=True,
            with_vectors=False,
            limit=100,
            offset=offset,
        )
        for p in points:
            source = (p.payload or {}).get("metadata", {}).get("source")
            if source:
                seen.add(source)
        if offset is None:
            break

    _sources_cache = sorted(seen)
    _sources_cache_at = time.monotonic()
    return _sources_cache
