"""Cached data access for the Streamlit app.

All heavy work happens once behind ``@st.cache_data``. The app reads the static
JSON produced by the core pipeline (``build_data.py`` and the insights pipeline)
and never scrapes or recomputes at request time. Live stock quotes come from the
separate quotes-api service.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# Resolve the shared data directory relative to this file, allowing an override
# for deployments where data is mounted elsewhere.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"
DATA_DIR = Path(os.getenv("DASHBOARD_DATA_DIR", str(_DEFAULT_DATA_DIR)))

FINANCIALS_PATH = DATA_DIR / "financials.json"
BUYBACKS_PATH = DATA_DIR / "buybacks.json"
INSIGHTS_PATH = DATA_DIR / "insights.json"

QUOTES_API_URL = os.getenv("QUOTES_API_URL", "http://localhost:8080")

_LEGACY_REMOVED_COLUMNS = {
    "Operating Income",
    "Operating Margin",
    "Net Income",
    "Net Margin",
}


@st.cache_data(show_spinner=False)
def _load_financials_cached(path_str: str, mtime_ns: int) -> pd.DataFrame:
    """Load the merged financials table with derived metrics."""
    path = Path(path_str)
    if not path.exists():
        return pd.DataFrame()
    df = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    if df.empty:
        return df
    # Guard against stale generated files that still carry removed metrics.
    drop_cols = [column for column in _LEGACY_REMOVED_COLUMNS if column in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    if "Reporting Year" not in df.columns and "AlignedYear" in df.columns:
        df = df.rename(
            columns={
                "Year": "Reporting Year",
                "Quarter": "Reporting Quarter",
                "Period": "Reporting Period",
                "Reported End": "Reporting End",
                "AlignedYear": "Year",
                "AlignedQuarter": "Quarter",
                "AlignedPeriod": "Period",
            }
        )
    if "Period" not in df.columns:
        df["Period"] = df["Year"].astype(str) + df["Quarter"].astype(str)
    if "Reporting Year" not in df.columns:
        df["Reporting Year"] = df["Year"]
    if "Reporting Quarter" not in df.columns:
        df["Reporting Quarter"] = df["Quarter"]
    if "Reporting Period" not in df.columns:
        df["Reporting Period"] = df["Reporting Year"].astype(str) + df["Reporting Quarter"].astype(str)
    if "Reporting End" not in df.columns:
        df["Reporting End"] = None
    return df.sort_values("Period")


def load_financials() -> pd.DataFrame:
    """Load the merged financials table with derived metrics."""
    if not FINANCIALS_PATH.exists():
        return pd.DataFrame()
    return _load_financials_cached(str(FINANCIALS_PATH), FINANCIALS_PATH.stat().st_mtime_ns)


def split_by_period(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (full-year, quarterly) views, dropping all-empty metric columns."""
    if df.empty:
        return df, df
    fy = df[df["Quarter"] == "FY"].dropna(axis=1, how="all").copy()
    q = df[df["Quarter"] != "FY"].dropna(axis=1, how="all").copy()
    return fy, q


@st.cache_data(show_spinner=False)
def load_buybacks() -> dict:
    """Load repurchase and share-sale history."""
    if not BUYBACKS_PATH.exists():
        return {"repurchases": [], "sales": []}
    return json.loads(BUYBACKS_PATH.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def load_insights() -> dict:
    """Load aligned-period insights with embedded reporting metadata."""
    if not INSIGHTS_PATH.exists():
        return {}
    return json.loads(INSIGHTS_PATH.read_text(encoding="utf-8"))


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_quotes(tickers: tuple[str, ...]) -> dict[str, dict]:
    """Fetch last-close quotes from the quotes-api, keyed by ticker.

    Returns an empty mapping if the service is unreachable so pages can degrade
    gracefully rather than error.
    """
    try:
        resp = requests.get(
            f"{QUOTES_API_URL}/quotes",
            params={"tickers": ",".join(tickers)},
            timeout=10,
        )
        resp.raise_for_status()
        return {q["ticker"]: q for q in resp.json().get("quotes", [])}
    except requests.RequestException:
        return {}


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_history(
    tickers: tuple[str, ...],
    start: str,
    ends: tuple[tuple[str, str], ...] = (),
) -> pd.DataFrame:
    """Fetch aligned daily closes since ``start`` as a date-indexed DataFrame.

    ``ends`` is an optional sequence of ``(ticker, YYYY-MM-DD)`` pairs. Each
    listed ticker's series is truncated after its end date, so a company that
    resumed repurchases stops contributing history at that point. Returns an
    empty frame if the quotes service is unreachable.
    """
    try:
        resp = requests.get(
            f"{QUOTES_API_URL}/history",
            params={"tickers": ",".join(tickers), "start": start},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json().get("history", {})
    except requests.RequestException:
        return pd.DataFrame()

    dates = payload.get("dates", [])
    closes = payload.get("closes", {})
    if not dates or not closes:
        return pd.DataFrame()

    df = pd.DataFrame(closes, index=pd.to_datetime(dates))
    df.index.name = "Date"
    for ticker, end in ends:
        if ticker in df.columns:
            df.loc[df.index > pd.Timestamp(end), ticker] = float("nan")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_live_quotes(
    tickers: tuple[str, ...],
) -> dict[str, dict]:
    """Fetch short-lived intraday quotes for the persistent ticker."""
    try:
        resp = requests.get(
            f"{QUOTES_API_URL}/live-quotes",
            params={"tickers": ",".join(tickers)},
            timeout=15,
        )
        resp.raise_for_status()

        return {
            quote["ticker"]: quote
            for quote in resp.json().get("quotes", [])
            if quote.get("price") is not None
        }

    except (requests.RequestException, ValueError, KeyError):
        return {}

EARNINGS_CACHE_TTL_SECONDS = 24 * 60 * 60
# How long to keep serving a last-known upcoming date after the API returns no
# date for it, before asking again.
EARNINGS_FALLBACK_TTL_SECONDS = 60 * 60
# Minimum wait between refetch attempts for tickers that have no cached date, so
# reruns don't hammer the API while it is cold, throttled, or down.
EARNINGS_RETRY_SECONDS = 5 * 60
_MARKET_TZ = ZoneInfo("America/New_York")


@st.cache_resource(show_spinner=False)
def _earnings_store() -> dict:
    """Process-wide per-ticker earnings cache shared across sessions and reruns."""
    return {"entries": {}, "last_attempt": {}, "lock": Lock()}


def _request_earnings(tickers: tuple[str, ...]) -> dict[str, dict] | None:
    """Return {ticker: item} for every ticker the API answered, or None on failure."""
    try:
        resp = requests.get(
            f"{QUOTES_API_URL}/earnings",
            params={"tickers": ",".join(tickers)},
            timeout=15,
        )
        resp.raise_for_status()
        return {item["ticker"]: item for item in resp.json().get("earnings", [])}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def _earnings_upcoming(item: dict) -> bool:
    last = item.get("date_to") or item.get("date_from")
    return bool(last) and last >= dt.datetime.now(_MARKET_TZ).date().isoformat()


def fetch_earnings_dates(tickers: tuple[str, ...]) -> dict[str, dict]:
    """Fetch earnings dates for the given tickers.

    Each ticker's date (past or future, as reported) is cached for 24 hours. When
    the API returns no date, a previously cached date is kept briefly only while
    it is still upcoming; otherwise the ticker is dropped. Failed requests leave
    the cache untouched and are retried at most every few minutes.
    """
    store = _earnings_store()
    now = time.time()
    with store["lock"]:
        entries: dict[str, tuple[float, dict]] = store["entries"]
        last_attempt: dict[str, float] = store["last_attempt"]
        stale = [t for t in tickers if t not in entries or now >= entries[t][0]]
        to_fetch = tuple(
            t for t in stale if now - last_attempt.get(t, 0.0) >= EARNINGS_RETRY_SECONDS
        )
        for t in to_fetch:
            last_attempt[t] = now

    if to_fetch:
        fetched = _request_earnings(to_fetch)
        if fetched is not None:
            with store["lock"]:
                for t in to_fetch:
                    item = fetched.get(t)
                    if item is None:
                        continue
                    if item.get("date_from"):
                        entries[t] = (now + EARNINGS_CACHE_TTL_SECONDS, item)
                    elif t in entries and _earnings_upcoming(entries[t][1]):
                        entries[t] = (now + EARNINGS_FALLBACK_TTL_SECONDS, entries[t][1])
                    else:
                        entries.pop(t, None)

    with store["lock"]:
        # Expired entries are still served until a refresh succeeds.
        return {t: entries[t][1] for t in tickers if t in entries}
