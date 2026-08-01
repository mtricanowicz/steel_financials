"""Parse SEC filing documents (HTML or PDF) into clean text."""

from __future__ import annotations

import io
import re

from bs4 import BeautifulSoup

_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Collapse redundant whitespace while preserving paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def html_to_text(html: bytes | str) -> str:
    """Extract readable text from an SEC HTML filing.

    Drops script/style noise and inline XBRL tags, keeping the human-readable
    narrative and table contents.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    # Inline XBRL wrappers (ix:*) carry no display text of their own.
    for tag in soup.find_all(re.compile(r"^ix:", re.I)):
        tag.unwrap()
    return clean_text(soup.get_text(separator="\n"))


def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF filing document."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return clean_text("\n\n".join(pages))


def document_to_text(content: bytes, primary_document: str) -> str:
    """Route a document to the correct parser based on its file extension."""
    name = primary_document.lower()
    if name.endswith(".pdf"):
        return pdf_to_text(content)
    return html_to_text(content)
