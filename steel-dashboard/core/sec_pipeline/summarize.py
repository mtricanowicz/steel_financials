"""RAG retrieval plus OpenAI summarization of a period's SEC filings."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Callable

from . import config
from .embed import EmbeddingFn, collection_size, retrieve_passages

# Approximate token budget for the retrieved context. Keeps requests well within
# the model's window while covering the period, and bounds latency and cost.
MAX_CONTEXT_TOKENS = 30_000

# Bound on generated tokens so output length (and cost) stays predictable.
MAX_OUTPUT_TOKENS = 1_800

# Fixed seed makes reruns comparable when tuning prompts.
SUMMARY_SEED = 7

SYSTEM_PROMPT = (
    "You are an expert financial analyst summarizing SEC filings and presenting "
    "them for public consumption. Accuracy is paramount, but you should provide "
    "interesting and revelatory insights. Language and style should be a cross "
    "between an investment analyst report and business media reporting."
)


@lru_cache(maxsize=1)
def _token_counter() -> Callable[[str], int]:
    """Return a token-counting function, using ``tiktoken`` when available."""
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(config.OPENAI_CHAT_MODEL)
        except Exception:
            enc = tiktoken.get_encoding("o200k_base")
        return lambda s: len(enc.encode(s))
    except Exception:
        # Rough fallback: ~4 characters per token.
        return lambda s: max(1, len(s) // 4)


def _retrieval_queries(
    steelmaker: str,
    name: str,
    label: str,
    report_period_end: datetime | None = None,
) -> list[str]:
    """Build several targeted queries spanning the reported topic areas."""
    if report_period_end is None:
        spec = config.PeriodSpec.from_label(label)
        _, end = spec.date_window()
        report_period_end = end
    period_end = f"{report_period_end:%B %d, %Y}"
    base = f"{steelmaker} ({name}) {label}"
    return [
        f"{base} financial results and operational highlights for the period ended {period_end}.",
        f"{base} revenue, operating income, net income, margins, unit costs, liquidity, and debt.",
        f"{base} capacity, utilization, volume indicators, and operating footprint.",
        f"{base} labor agreements, unions, workforce, and personnel updates.",
        f"{base} executive leadership changes, CEO, CFO, board, and management appointments.",
        f"{base} market footprint changes, expansions, partnerships, and geographic strategy.",
        f"{base} commercial strategy, forward guidance, and shareholder returns.",
    ]


def _source_tag(meta: dict) -> str:
    """Compact provenance tag prepended to each excerpt."""
    form = meta.get("form") or "filing"
    date = meta.get("filing_date") or "date unknown"
    return f"[{form} filed {date}]"


def _build_context(passages: list[tuple[str, dict]]) -> str:
    """Assemble tagged excerpts up to the token budget, in relevance order."""
    count = _token_counter()
    blocks: list[str] = []
    used = 0
    for text, meta in passages:
        block = f"{_source_tag(meta)}\n{text}"
        tokens = count(block)
        if blocks and used + tokens > MAX_CONTEXT_TOKENS:
            break
        blocks.append(block)
        used += tokens
    return "\n\n".join(blocks)


def _user_prompt(steelmaker: str, name: str, label: str, context: str) -> str:
    return f"""You are an expert financial analyst summarizing SEC filings for {name} ({steelmaker}) covering {label}. Be thorough and honest, critical when warranted while recognizing genuine successes. Be neither a cheerleader nor a naysayer.

Below are relevant filing excerpts. Each excerpt is prefixed with a source tag in the form [FORM filed YYYY-MM-DD]. Use those tags to order events chronologically and to ground the timing of developments; never invent a date that is not present in a tag or the text.

{context}

Analyze all of the provided material, which is drawn from the issuer's 10-Q, 10-K, and 8-K filings and related exhibits for the period.
Provide the top insights for {label}. Focus on data from {label} and ignore discussion of earlier periods unless it provides meaningful context for current results. Provide up to 10 insights. Insights should cover key developments across these areas as supported by the excerpts: financial performance, operations, commercial strategy, labor, executive and personnel changes, and route network. Do NOT include an area if there is no relevant data or nothing meaningful to report.
Do NOT under any circumstances fabricate names, dates, or numerical figures. Every figure and name must appear in the provided excerpts. Fabrication includes any invented placeholder such as "John Doe" or "Jane Doe". If a detail is not supported by the excerpts, omit it.
Clearly distinguish reported actual results from forward-looking guidance and projections. Do not present management outlook, targets, or estimates as realized results; label them as guidance or expectations.
Highlight any major events, quantify their impact, and provide supporting context.
    Format the response as markdown grouped by topic. For each area that has content, add a '### ' heading named "<Area> Insights" (for example "### Financial Insights") followed by a numbered list. Begin each numbered item with a short bold takeaway (for example "**Revenue rose 5% year over year.**") and then the supporting detail. Present insights in chronological order where possible. Each item should fully detail the insight while remaining easy to read and digest. Include relevant names when discussing personnel and accurate figures when discussing financial or other metrics. Use context appropriate units (e.g. use billions instead of thousand millions). When mentioning dollar amounts or any literal '$' character in markdown text, escape it as '\$' so it renders correctly in markdown and is preserved in the generated JSON.
End the response with a '### Wrap Up' heading followed by a single paragraph that summarizes the results and the positive and negative elements of forward guidance."""


def summarize_period(
    steelmaker: str,
    label: str,
    collection_name: str,
    embedder: EmbeddingFn,
    per_query_k: int | None = None,
    report_period_end: datetime | None = None,
) -> str:
    """Retrieve the most relevant filing text and generate a markdown summary."""
    from openai import OpenAI

    name = config.STEELMAKER_NAMES.get(steelmaker, steelmaker)
    queries = _retrieval_queries(steelmaker, name, label, report_period_end)
    if per_query_k is None:
        # A moderate per-query depth; the token budget below is the real cap on
        # how much context reaches the model after de-duplication.
        per_query_k = max(1, 30 + int(0.02 * collection_size(collection_name)))
    passages = retrieve_passages(collection_name, queries, embedder, k=per_query_k)
    if not passages:
        raise ValueError(f"No indexed content found for {collection_name}")
    context = _build_context(passages)
    client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=60.0, max_retries=5)
    resp = client.chat.completions.create(
        model=config.OPENAI_CHAT_MODEL,
        temperature=0.3,
        seed=SUMMARY_SEED,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(steelmaker, name, label, context)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

