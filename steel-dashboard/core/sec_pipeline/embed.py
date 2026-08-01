"""Embeddings and a Chroma vector store for retrieval.

LangChain is intentionally not used. We talk to ``chromadb`` and the OpenAI SDK
directly, which keeps the dependency surface small and the data flow explicit.
The embedding backend is selectable via ``EMBEDDING_BACKEND`` (``openai`` or
``local``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import chromadb
from chromadb.api.models.Collection import Collection

from . import config


class EmbeddingFn(Protocol):
    def __call__(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class Chunk:
    """A unit of text plus its provenance metadata."""

    text: str
    metadata: dict[str, str | int]


def _openai_embedder() -> EmbeddingFn:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    model = config.OPENAI_EMBEDDING_MODEL

    def embed(texts: Sequence[str]) -> list[list[float]]:
        resp = client.embeddings.create(model=model, input=list(texts))
        return [d.embedding for d in resp.data]

    return embed


def _local_embedder() -> EmbeddingFn:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config.LOCAL_EMBEDDING_MODEL)

    def embed(texts: Sequence[str]) -> list[list[float]]:
        return model.encode(list(texts), normalize_embeddings=True).tolist()

    return embed


def get_embedder() -> EmbeddingFn:
    """Return the configured embedding function."""
    if config.EMBEDDING_BACKEND == "openai":
        return _openai_embedder()
    return _local_embedder()


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def build_collection(
    collection_name: str,
    chunks: list[Chunk],
    embedder: EmbeddingFn,
    batch_size: int = 100,
) -> Collection:
    """Create (or replace) a Chroma collection and add embedded chunks."""
    client = _client()
    # Rebuild from scratch so re-runs are deterministic.
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        texts = [c.text for c in batch]
        collection.add(
            ids=[f"{collection_name}-{start + i}" for i in range(len(batch))],
            documents=texts,
            embeddings=embedder(texts),
            metadatas=[c.metadata for c in batch],
        )
    return collection


def retrieve(
    collection_name: str, query: str, embedder: EmbeddingFn, k: int = 12
) -> list[str]:
    """Return the ``k`` most relevant chunk texts for ``query``."""
    collection = _client().get_collection(collection_name)
    result = collection.query(
        query_embeddings=embedder([query]), n_results=k, include=["documents"]
    )
    documents = result.get("documents") or [[]]
    return documents[0]


def collection_size(collection_name: str) -> int:
    """Return the number of stored chunks in a collection."""
    return _client().get_collection(collection_name).count()


def _dedup_key(text: str) -> str:
    """Normalize text so duplicate passages collapse to one key."""
    return " ".join(text.split()).lower()


def retrieve_passages(
    collection_name: str,
    queries: Sequence[str],
    embedder: EmbeddingFn,
    k: int,
) -> list[tuple[str, dict]]:
    """Retrieve unique ``(text, metadata)`` passages across one or more queries.

    All queries are embedded and searched in a single call. Results are merged by
    interleaving each query's ranked hits (so every query contributes coverage
    before any one query dominates) and de-duplicated by normalized text.
    """
    collection = _client().get_collection(collection_name)
    embeddings = embedder(list(queries))
    result = collection.query(
        query_embeddings=embeddings,
        n_results=k,
        include=["documents", "metadatas"],
    )
    docs_per_query = result.get("documents") or []
    metas_per_query = result.get("metadatas") or []
    seen: set[str] = set()
    passages: list[tuple[str, dict]] = []
    max_len = max((len(d) for d in docs_per_query), default=0)
    for rank in range(max_len):
        for qi, docs in enumerate(docs_per_query):
            if rank >= len(docs):
                continue
            text = docs[rank]
            key = _dedup_key(text)
            if key in seen:
                continue
            seen.add(key)
            metas = metas_per_query[qi] if qi < len(metas_per_query) else []
            meta = metas[rank] if rank < len(metas) else {}
            passages.append((text, meta or {}))
    return passages
