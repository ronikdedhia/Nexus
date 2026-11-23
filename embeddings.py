"""FastEmbed embeddings for LangChain, without the deprecated langchain-community package.

Makes the same fastembed calls as langchain_community's FastEmbedEmbeddings (embed() for documents,
query_embed() for queries), so vectors already stored in Qdrant stay compatible."""
from langchain_core.embeddings import Embeddings

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class FastEmbedEmbeddings(Embeddings):
    def __init__(self, model_name: str = MODEL_NAME, batch_size: int = 256):
        from fastembed import TextEmbedding  # imported lazily: loading the model is slow

        self.model = TextEmbedding(model_name=model_name)
        self.batch_size = batch_size

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [e.tolist() for e in self.model.embed(texts, batch_size=self.batch_size)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text, batch_size=self.batch_size))).tolist()
