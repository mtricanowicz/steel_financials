"""Split cleaned filing text into overlapping chunks for embedding."""

from __future__ import annotations

import re

# Prefer to break on paragraph boundaries, then sentences, then hard length.
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_units(text: str) -> list[str]:
    units: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= 2000:
            units.append(para)
        else:
            units.extend(s.strip() for s in _SENTENCE_RE.split(para) if s.strip())
    return units


def _hard_wrap(unit: str, chunk_size: int, overlap: int) -> list[str]:
    """Slice an oversized unit into fixed windows no larger than ``chunk_size``.

    Some filing text (tables, exhibits) has no paragraph or sentence breaks, so a
    single unit can exceed the embedding model's input limit. Windows carry
    ``overlap`` characters of context between them.
    """
    step = max(1, chunk_size - overlap)
    pieces: list[str] = []
    for start in range(0, len(unit), step):
        pieces.append(unit[start : start + chunk_size])
        if start + chunk_size >= len(unit):
            break
    return pieces


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    """Chunk text to roughly ``chunk_size`` characters with ``overlap`` carryover.

    Chunks respect paragraph and sentence boundaries where possible, keeping each
    chunk to a size that is comfortable for embeddings and LLM context.
    """
    if not text:
        return []
    units = _split_units(text)
    chunks: list[str] = []
    current = ""
    for unit in units:
        # A unit larger than the target is hard-split so no single chunk can
        # exceed the embedding model's input limit.
        if len(unit) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_wrap(unit, chunk_size, overlap))
            continue
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= chunk_size:
            current = f"{current}\n{unit}"
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n{unit}".strip() if tail else unit
    if current:
        chunks.append(current)
    return chunks
