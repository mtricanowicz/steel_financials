"""Embeddings and a Chroma vector store for retrieval.

LangChain is intentionally not used. We talk to ``chromadb`` and the OpenAI SDK
directly, which keeps the dependency surface small and the data flow explicit.
The embedding backend is selectable via ``EMBEDDING_BACKEND`` (``openai`` or
``local``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

import chromadb
from chromadb.api.models.Collection import Collection

from . import config


class EmbeddingFn(Protocol):
    def __call__(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class Chunk:
    """A unit of text plus its provenance metadata."""

    text: str
    metadata: dict[str, str | int | float | bool]


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


def _shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[index : index + size]) for index in range(len(words) - size + 1)}


def _near_duplicate(text: str, existing: Sequence[set[tuple[str, ...]]]) -> bool:
    shingles = _shingles(text)
    if not shingles:
        return False
    return any(
        len(shingles & other) / min(len(shingles), len(other)) >= 0.85
        for other in existing
        if other
    )


def retrieve_passages(
    collection_name: str,
    queries: Sequence[str],
    embedder: EmbeddingFn,
    k: int,
    query_weights: Mapping[int, float] | None = None,
) -> list[tuple[str, dict]]:
    """Fuse ranked query results, then return source-aware unique passages."""
    collection = _client().get_collection(collection_name)
    embeddings = embedder(list(queries))
    result = collection.query(
        query_embeddings=embeddings,
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    docs_per_query = result.get("documents") or []
    metas_per_query = result.get("metadatas") or []
    distances_per_query = result.get("distances") or []
    candidates: list[dict] = []
    exact_matches: dict[tuple[str | None, str], dict] = {}

    def find_near_match(text: str, metadata: dict) -> dict | None:
        shingles = _shingles(text)
        if not shingles:
            return None
        for candidate in candidates:
            candidate_metadata = candidate["metadata"]
            if candidate_metadata.get("source_id") != metadata.get("source_id"):
                continue
            candidate_index = candidate_metadata.get("chunk_index")
            current_index = metadata.get("chunk_index")
            if isinstance(candidate_index, int) and isinstance(current_index, int):
                if abs(candidate_index - current_index) > 1:
                    continue
            if _near_duplicate(text, [candidate["shingles"]]):
                return candidate
        return None

    max_len = max((len(d) for d in docs_per_query), default=0)
    for rank in range(max_len):
        for query_index, docs in enumerate(docs_per_query):
            if rank >= len(docs):
                continue
            text = docs[rank]
            metas = metas_per_query[query_index] if query_index < len(metas_per_query) else []
            meta = metas[rank] if rank < len(metas) else {}
            distances = (
                distances_per_query[query_index] if query_index < len(distances_per_query) else []
            )
            distance = distances[rank] if rank < len(distances) else None
            metadata = dict(meta or {})
            key = (metadata.get("source_id"), _dedup_key(text))
            candidate = exact_matches.get(key) or find_near_match(text, metadata)
            if candidate is None:
                candidate = {
                    "text": text,
                    "metadata": metadata,
                    "shingles": _shingles(text),
                    "occurrences": [],
                }
                candidates.append(candidate)
                exact_matches[key] = candidate
            candidate["occurrences"].append(
                {"query_index": query_index, "query_rank": rank, "distance": distance}
            )

    passages: list[tuple[str, dict]] = []
    weights = query_weights or {}
    for candidate in candidates:
        occurrences = candidate["occurrences"]
        score = sum(
            weights.get(occurrence["query_index"], 1.0) / (60 + occurrence["query_rank"])
            for occurrence in occurrences
        )
        best = min(occurrences, key=lambda occurrence: occurrence["query_rank"])
        metadata = candidate["metadata"]
        metadata["query_index"] = best["query_index"]
        metadata["query_rank"] = best["query_rank"]
        metadata["query_support"] = len(occurrences)
        metadata["query_indices"] = sorted({item["query_index"] for item in occurrences})
        metadata["retrieval_score"] = round(score, 6)
        if best["distance"] is not None:
            metadata["retrieval_distance"] = best["distance"]
        passages.append((candidate["text"], metadata))
    passages.sort(key=lambda passage: passage[1]["retrieval_score"], reverse=True)
    return passages
