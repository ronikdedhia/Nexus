import os
from langchain_qdrant import QdrantVectorStore
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.tools import tool

_store: QdrantVectorStore | None = None


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
