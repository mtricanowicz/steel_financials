"""End-to-end orchestration: scrape -> chunk -> embed -> summarize -> persist.

The pipeline is idempotent: existing summaries are preserved and skipped unless
``overwrite`` is set. Results are written to ``data/generated/insights.json`` in
the shape ``{ticker: {year: {period: markdown}}}`` consumed by both front ends.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Iterable

from . import config
from .chunk import chunk_text
from .edgar_client import EdgarClient
from .embed import Chunk, build_collection, get_embedder
from .parse import document_to_text
from .summarize import summarize_period

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sec_pipeline")


def _load_summaries() -> dict:
    if config.SUMMARIES_PATH.exists():
        return json.loads(config.SUMMARIES_PATH.read_text(encoding="utf-8"))
    return {}


def _save_summaries(summaries: dict) -> None:
    config.SUMMARIES_PATH.write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _has_summary(summaries: dict, steelmaker: str, spec: config.PeriodSpec) -> bool:
    return bool(
        summaries.get(steelmaker, {}).get(str(spec.year), {}).get(spec.period)
    )


def _store_summary(summaries: dict, steelmaker: str, spec: config.PeriodSpec, text: str) -> None:
    summaries.setdefault(steelmaker, {}).setdefault(str(spec.year), {})[spec.period] = text


def build_period_chunks(
    client: EdgarClient, cik: str, spec: config.PeriodSpec
) -> list[Chunk]:
    """Download and chunk every relevant filing for one ticker-period."""
    start, end = spec.date_window()
    filings = client.filings_in_window(cik, start, end, config.RELEVANT_FORMS)
    chunks: list[Chunk] = []
    for filing in filings:
        try:
            content = client.fetch_document(cik, filing)
            text = document_to_text(content, filing.primary_document)
        except Exception as exc:  # noqa: BLE001 - log and continue on a bad doc
            log.warning("Skipping %s %s: %s", filing.form, filing.accession, exc)
            continue
        for piece in chunk_text(text):
            chunks.append(
                Chunk(
                    text=piece,
                    metadata={
                        "form": filing.form,
                        "accession": filing.accession,
                        "filing_date": filing.filing_date.strftime("%Y-%m-%d"),
                    },
                )
            )
    return chunks


def run(
    steelmakers: Iterable[str],
    years: Iterable[int],
    periods: Iterable[str],
    overwrite: bool = False,
) -> dict:
    """Run the pipeline for the given steelmakers/years/periods and persist results."""
    client = EdgarClient()
    ciks = client.resolve_ciks(list(steelmakers))
    embedder = get_embedder()
    summaries = _load_summaries()
    specs = config.build_periods(list(years), list(periods))

    for steelmaker in steelmakers:
        cik = ciks[steelmaker]
        for spec in specs:
            if not overwrite and _has_summary(summaries, steelmaker, spec):
                log.info("Skip %s %s (already summarized)", steelmaker, spec.label)
                continue
            log.info("Processing %s %s", steelmaker, spec.label)
            chunks = build_period_chunks(client, cik, spec)
            if not chunks:
                log.warning("No filings found for %s %s", steelmaker, spec.label)
                continue
            collection_name = f"{steelmaker}{spec.label}".lower()
            build_collection(collection_name, chunks, embedder)
            try:
                text = summarize_period(steelmaker, spec.label, collection_name, embedder)
            except Exception as exc:  # noqa: BLE001
                log.error("Summarization failed for %s %s: %s", steelmaker, spec.label, exc)
                continue
            _store_summary(summaries, steelmaker, spec, text)
            _save_summaries(summaries)  # persist incrementally
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SEC insights pipeline.")
    parser.add_argument("--steelmakers", nargs="+", default=list(config.STEELMAKER_NAMES))
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--periods", nargs="+", default=list(config.QUARTERS))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    run(args.steelmakers, args.years, args.periods, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
