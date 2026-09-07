"""Measure summary specificity, repetition, attribution, and formatting quality."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

BANNED_PHRASES = (
    "this reflects", "reflecting", "driven by", "primarily driven by", "indicating",
    "marking", "showcasing", "robust", "solid", "strong", "remarkable", "significant",
    "well-positioned", "underscores the company's commitment", "aims to enhance",
)
CAUSAL_PATTERNS = re.compile(r"\b(?:driven by|primarily driven by|due to|because of|as a result of|attributed to|resulting from|reflecting)\b", re.I)
ATTRIBUTION_PATTERNS = re.compile(r"\b(?:management|the company|the filing|the report|according to|stated|said|reported|disclosed|noted|explained|announced|estimated|expects?|project(?:ed|s)?|guidance)\b", re.I)
FIGURE_PATTERN = re.compile(r"(?:\$\\?\s?\(?\d[\d,.]*(?:\.\d+)?\)?|\b\d[\d,.]*%|\b\d[\d,.]*\s*(?:million|billion|thousand|tons?|shipments?|points?|facilities|employees?)\b)", re.I)
WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
HEADING_PATTERN = re.compile(r"^#{3,6}\s+(.+?)\s*$", re.M)
ITEM_PATTERN = re.compile(r"^\s*\d+\.\s+(.*?)(?=^\s*\d+\.\s+|^###\s+|\Z)", re.M | re.S)
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
METRIC_FAMILIES = {
    "shipments_and_production": re.compile(r"\b(?:shipments?|tons?|production|utilization|capacity)\b", re.I),
    "energy_and_raw_materials": re.compile(r"\b(?:energy|scrap|iron ore|coal|coke|natural gas|raw material)\b", re.I),
    "revenue_and_pricing": re.compile(r"\b(?:revenue|sales|realized price|pricing|price per ton)\b", re.I),
    "costs_and_margins": re.compile(r"\b(?:cost|expense|margin|profit|income)\b", re.I),
    "liquidity_and_debt": re.compile(r"\b(?:liquidity|cash|debt|borrowings|credit facility)\b", re.I),
}
SECONDARY_HEADINGS = {"Labor Insights", "Corporate and Risk Insights"}


def _words(text: str) -> list[str]:
    return [word.lower() for word in WORD_PATTERN.findall(text)]


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _outside_quotes(text: str) -> Iterable[str]:
    for index, fragment in enumerate(re.split(r'("[^"]*"|“[^”]*”)', text)):
        if index % 2 == 0:
            yield fragment


def _phrase_hits(text: str) -> int:
    return sum(len(re.findall(rf"\b{re.escape(phrase)}\b", fragment, re.I)) for fragment in _outside_quotes(text) for phrase in BANNED_PHRASES)


def _causal_hits(text: str) -> int:
    return sum(
        1 for sentence in _sentences(text)
        if any(CAUSAL_PATTERNS.search(fragment) for fragment in _outside_quotes(sentence))
        and not any(ATTRIBUTION_PATTERNS.search(fragment) for fragment in _outside_quotes(sentence))
    )


def _items(text: str) -> list[str]:
    return [match.group(1).strip() for match in ITEM_PATTERN.finditer(text)]


def _items_by_heading(text: str) -> list[tuple[str, str]]:
    sections = re.split(r"^###\s+(.+?)\s*$", text, flags=re.M)
    return [(sections[index].strip(), item) for index in range(1, len(sections), 2) for item in _items(sections[index + 1])]


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set, right_set = set(left), set(right)
    return 1.0 if not left_set and not right_set else len(left_set & right_set) / len(left_set | right_set)


def _summary_metrics(text: str) -> dict[str, Any]:
    words, items = _words(text), _items(text)
    figures = [len(FIGURE_PATTERN.findall(item)) for item in items]
    overlaps = []
    for item in items:
        takeaway = BOLD_PATTERN.search(item)
        body = item if not takeaway else f"{item[:takeaway.start()]} {item[takeaway.end():]}"
        overlaps.append(_jaccard(_words(takeaway.group(1)) if takeaway else [], _words(body)))
    family_counts: Counter[str] = Counter()
    for item in items:
        family_counts.update(name for name, pattern in METRIC_FAMILIES.items() if pattern.search(item))
    sections = re.split(r"^###\s+(.+?)\s*$", text, flags=re.M)
    wrap_up = next((sections[index + 1] for index in range(1, len(sections), 2) if sections[index].strip() == "Wrap Up"), "")
    other_body = "\n".join(sections[index + 1] for index in range(1, len(sections), 2) if sections[index].strip() != "Wrap Up")
    body_figures = set(FIGURE_PATTERN.findall(other_body))
    return {
        "word_count": len(words), "item_count": len(items), "banned_phrase_hits": _phrase_hits(text),
        "banned_phrase_hits_per_1000_words": round(_phrase_hits(text) * 1000 / max(1, len(words)), 3),
        "figures_per_item": round(sum(figures) / max(1, len(figures)), 3),
        "items_with_zero_figures": sum(value == 0 for value in figures),
        "bold_body_jaccard": round(sum(overlaps) / max(1, len(overlaps)), 3),
        "unattributed_causal_phrases": _causal_hits(text),
        "truncated": bool(text.rstrip()) and text.rstrip()[-1] not in ".!?)]}\"'`",
        "headings": HEADING_PATTERN.findall(text),
        "repeated_metric_families": {name: count for name, count in family_counts.items() if count > 1},
        "secondary_section_items": sum(heading in SECONDARY_HEADINGS for heading, _ in _items_by_heading(text)),
        "wrap_up_reused_figures": sum(figure in body_figures for figure in FIGURE_PATTERN.findall(wrap_up)),
    }


def load_summaries(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {f"{ticker} {year} {period}": text for ticker, years in raw.items() for year, periods in years.items() for period, text in periods.items() if isinstance(text, str)}


def _ngrams(words: list[str], size: int = 4) -> Counter[tuple[str, ...]]:
    return Counter(tuple(words[index:index + size]) for index in range(len(words) - size + 1))


def _discovery_words(text: str) -> list[str]:
    content = re.sub(r"^#{1,6}.*$|^\s*\d+\.\s+", "", text, flags=re.M)
    return _words(content)


def _aggregate(metrics: dict[str, dict[str, Any]], texts: dict[str, str]) -> dict[str, Any]:
    total_words, total_items = sum(item["word_count"] for item in metrics.values()), sum(item["item_count"] for item in metrics.values())
    counts = Counter(phrase for text in texts.values() for phrase in _ngrams(_discovery_words(text)))
    return {"summary_count": len(metrics), "word_count": total_words, "item_count": total_items, "banned_phrase_hits_per_1000_words": round(sum(item["banned_phrase_hits"] for item in metrics.values()) * 1000 / max(1, total_words), 3), "figures_per_item": round(sum(len(FIGURE_PATTERN.findall(item)) for text in texts.values() for item in _items(text)) / max(1, total_items), 3), "items_with_zero_figures": sum(item["items_with_zero_figures"] for item in metrics.values()), "bold_body_jaccard": round(sum(item["bold_body_jaccard"] for item in metrics.values()) / max(1, len(metrics)), 3), "unattributed_causal_phrases": sum(item["unattributed_causal_phrases"] for item in metrics.values()), "truncated_summaries": sum(item["truncated"] for item in metrics.values()), "cross_summary_repeated_4gram_rate": round(sum(count for count in counts.values() if count > 1) / max(1, sum(counts.values())), 3)}


def discover(texts: dict[str, str], limit: int = 50) -> dict[str, Any]:
    spreads: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
    counts: Counter[tuple[str, ...]] = Counter()
    openers: Counter[str] = Counter()
    for key, text in texts.items():
        sentences = _sentences(re.sub(r"^#{1,6}.*$", "", text, flags=re.M))
        if sentences:
            openers[" ".join(_words(sentences[0])[:5])] += 1
        for phrase in _ngrams(_discovery_words(text)):
            counts[phrase] += 1
            spreads[phrase].add(key.split()[0])
    reused = [{"phrase": " ".join(phrase), "count": count, "issuer_spread": len(spreads[phrase])} for phrase, count in counts.most_common() if count > 1]
    return {"sentence_openers": openers.most_common(limit), "reused_4grams": reused[:limit]}


def lint(path: Path) -> dict[str, Any]:
    texts = load_summaries(path)
    metrics = {key: _summary_metrics(text) for key, text in texts.items()}
    return {"path": str(path), "per_summary": metrics, "aggregate": _aggregate(metrics, texts)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint generated SEC summary markdown.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    result = lint(args.path)
    if args.baseline and args.compare:
        baseline, comparison = lint(args.baseline)["aggregate"], lint(args.compare)["aggregate"]
        result["comparison"] = {key: {"baseline": baseline.get(key), "compare": comparison.get(key)} for key in sorted(set(baseline) | set(comparison))}
    if args.discover:
        result["discover"] = discover(load_summaries(args.path))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()