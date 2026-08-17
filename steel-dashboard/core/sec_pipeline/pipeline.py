"""End-to-end orchestration: scrape -> chunk -> embed -> summarize -> persist.

The pipeline is idempotent: existing summaries are preserved and skipped unless
``overwrite`` is set. Results are written to ``data/generated/insights.json`` in
the shape ``{ticker: {year: {period: markdown}}}`` consumed by both front ends.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from typing import Iterable

from dateutil.relativedelta import relativedelta

from . import config
from .chunk import chunk_text
from .edgar_client import EdgarClient, Filing
from .embed import Chunk, build_collection, get_embedder
from .parse import document_to_text
from .summarize import summarize_period

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sec_pipeline")

_FORM_FILING_LAG_DAYS = {"10-Q": 70, "10-K": 95, "8-K": 45}
_REPORT_DATE_TOLERANCE_DAYS = 7
_WINDOW_LEAD_DAYS = 5


def _expected_report_date(spec: config.PeriodSpec, fiscal_year_end: datetime) -> datetime:
    """Expected report-date anchor for a fiscal year/period."""
    if spec.period == "FY":
        return datetime(spec.year, fiscal_year_end.month, fiscal_year_end.day)
    offsets = {"Q1": 9, "Q2": 6, "Q3": 3, "Q4": 0}
    end = datetime(spec.year, fiscal_year_end.month, fiscal_year_end.day)
    return end - relativedelta(months=offsets[spec.period])


def _infer_fiscal_year_end(filings: list[Filing]) -> datetime:
    """Infer fiscal year-end month/day from the latest annual filing report date."""
    annual = [f for f in filings if f.form == "10-K" and f.report_date is not None]
    if not annual:
        # Calendar fallback keeps behavior stable for issuers without reportDate.
        return datetime(2000, 12, 31)
    latest = max(annual, key=lambda f: f.filing_date)
    assert latest.report_date is not None  # narrowed above
    return datetime(2000, latest.report_date.month, latest.report_date.day)


def _days_between(a: datetime, b: datetime) -> int:
    return abs((a - b).days)


def _filings_by_report_date(
    filings: list[Filing],
    spec: config.PeriodSpec,
    fiscal_year_end: datetime,
) -> list[Filing]:
    """Select filings matching the target reporting period by report date first."""
    target = _expected_report_date(spec, fiscal_year_end)
    wanted_forms = set(config.RELEVANT_FORMS)
    matched: list[Filing] = []
    for filing in filings:
        if filing.form not in wanted_forms or filing.report_date is None:
            continue
        if _days_between(filing.report_date, target) <= _REPORT_DATE_TOLERANCE_DAYS:
            matched.append(filing)
    return sorted(matched, key=lambda f: f.filing_date)


def _filings_by_filing_window(
    filings: list[Filing], spec: config.PeriodSpec, fiscal_year_end: datetime
) -> list[Filing]:
    """Fallback selection using form-aware filing lags after expected period end."""
    target = _expected_report_date(spec, fiscal_year_end)
    matched: list[Filing] = []
    for filing in filings:
        if filing.form not in config.RELEVANT_FORMS:
            continue
        lag_days = _FORM_FILING_LAG_DAYS.get(filing.form, 70)
        start = target - relativedelta(days=_WINDOW_LEAD_DAYS)
        end = target + relativedelta(days=lag_days)
        if start <= filing.filing_date <= end:
            matched.append(filing)
    return sorted(matched, key=lambda f: f.filing_date)


def _select_period_filings(client: EdgarClient, cik: str, spec: config.PeriodSpec) -> list[Filing]:
    """Period-first filing selection with date-window fallback."""
    all_filings = [f for f in client.list_filings(cik) if f.form in config.RELEVANT_FORMS]
    fiscal_year_end = _infer_fiscal_year_end(all_filings)
    primary = _filings_by_report_date(all_filings, spec, fiscal_year_end)
    if primary:
        return primary
    fallback = _filings_by_filing_window(all_filings, spec, fiscal_year_end)
    if fallback:
        return fallback
    # Last-resort compatibility fallback to previous behavior.
    start, end = spec.date_window()
    return client.filings_in_window(cik, start, end, config.RELEVANT_FORMS)


def _representative_report_date(filings: list[Filing]) -> datetime | None:
    report_dates = [f.report_date for f in filings if f.report_date is not None]
    if not report_dates:
        return None
    return max(report_dates)


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
) -> tuple[list[Chunk], datetime | None]:
    """Download and chunk every relevant filing for one ticker-period."""
    filings = _select_period_filings(client, cik, spec)
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
                        "report_date": filing.report_date.strftime("%Y-%m-%d")
                        if filing.report_date
                        else None,
                    },
                )
            )
    return chunks, _representative_report_date(filings)


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
            chunks, report_date = build_period_chunks(client, cik, spec)
            if not chunks:
                log.warning("No filings found for %s %s", steelmaker, spec.label)
                continue
            collection_name = f"{steelmaker}{spec.label}".lower()
            build_collection(collection_name, chunks, embedder)
            try:
                text = summarize_period(
                    steelmaker,
                    spec.label,
                    collection_name,
                    embedder,
                    report_period_end=report_date,
                )
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
