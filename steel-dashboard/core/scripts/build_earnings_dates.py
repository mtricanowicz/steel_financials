"""Build ``data/generated/earnings_dates.json`` from the yfinance earnings calendar.

The dashboard reads this file instead of querying a live service on page load.
The output maps each ticker to ``{"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}``
(``date_to`` equals ``date_from`` for a confirmed date, or ends an estimated range).

Last-known-good behaviour: a ticker whose fetch fails or comes back empty keeps its
stored entry, and a stored upcoming date is never replaced by a date in the past.
The file carries no timestamps, so an unchanged calendar yields a byte-identical
file and the workflow has nothing to commit.

Exits non-zero only when no ticker could be fetched at all (provider down), so a
bad run never publishes and never commits.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_earnings_dates")

OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "generated" / "earnings_dates.json"
# Active (non-defunct) tickers shown in the dashboard sidebar.
DEFAULT_TICKERS = ["NUE", "STLD", "CLF", "CMC"]
MARKET_TZ = ZoneInfo("America/New_York")
FETCH_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
REQUEST_PAUSE_SECONDS = 1.0


def parse_earnings_dates(calendar: object) -> tuple[str, str] | None:
    """Return (first, last) ISO dates from yfinance calendar data, or None if empty."""
    if isinstance(calendar, pd.DataFrame):
        if "Earnings Date" in calendar.index:
            values = calendar.loc["Earnings Date"].tolist()
        elif "Earnings Date" in calendar.columns:
            values = calendar["Earnings Date"].tolist()
        else:
            return None
    elif isinstance(calendar, dict):
        values = calendar.get("Earnings Date")
    else:
        return None

    if values is None:
        return None
    if not isinstance(values, (list, tuple, pd.Series)):
        values = [values]

    dates: list[dt.date] = []
    for value in values:
        parsed = pd.to_datetime(value, errors="coerce")
        if not pd.isna(parsed):
            dates.append(parsed.date())
    if not dates:
        return None
    dates.sort()
    return dates[0].isoformat(), dates[-1].isoformat()


def fetch_ticker(ticker: str) -> dict[str, str] | None:
    """Fetch one ticker's dates, retrying exceptions. None means no usable result."""
    import yfinance as yf

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            parsed = parse_earnings_dates(yf.Ticker(ticker).calendar)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: fetch failed (try %d/%d): %s", ticker, attempt, FETCH_ATTEMPTS, exc)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue
        if parsed is None:
            log.warning("%s: calendar returned no date", ticker)
            return None
        return {"date_from": parsed[0], "date_to": parsed[1]}
    return None


def _is_upcoming(entry: dict[str, str], today: str) -> bool:
    return (entry.get("date_to") or entry["date_from"]) >= today


def merge_entry(
    ticker: str, stored: dict[str, str] | None, fetched: dict[str, str] | None, today: str
) -> dict[str, str] | None:
    """Pick the entry to keep for a ticker, preferring last-known-good data."""
    if fetched is None:
        return stored
    if stored is not None and _is_upcoming(stored, today) and not _is_upcoming(fetched, today):
        log.info("%s: ignoring past date %s; keeping upcoming %s", ticker, fetched, stored)
        return stored
    return fetched


def load_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Existing %s is not valid JSON; starting fresh", path)
        return {}
    return {
        t: e for t, e in data.items() if isinstance(e, dict) and isinstance(e.get("date_from"), str)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    tickers = list(dict.fromkeys(t.upper() for t in args.tickers))
    existing = load_existing(args.output)
    today = dt.datetime.now(MARKET_TZ).date().isoformat()

    result: dict[str, dict[str, str]] = {}
    fetched_count = 0
    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(REQUEST_PAUSE_SECONDS)
        fetched = fetch_ticker(ticker)
        fetched_count += fetched is not None
        entry = merge_entry(ticker, existing.get(ticker), fetched, today)
        if entry is not None:
            result[ticker] = entry

    if fetched_count == 0:
        log.error("No earnings dates could be fetched for any ticker; leaving file untouched")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dict(sorted(result.items())), indent=2) + "\n", encoding="utf-8"
    )
    log.info("Fetched %d/%d tickers; wrote %s", fetched_count, len(tickers), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
