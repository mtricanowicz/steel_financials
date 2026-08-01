"""Cached data access for the Streamlit app.

All heavy work happens once behind ``@st.cache_data``. The app reads the static
JSON produced by the core pipeline (``build_data.py`` and the insights pipeline)
and never scrapes or recomputes at request time. Live stock quotes come from the
separate quotes-api service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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
    if "Period" not in df.columns:
        df["Period"] = df["Year"].astype(str) + df["Quarter"].astype(str)
    if "AlignedYear" not in df.columns:
        df["AlignedYear"] = df["Year"]
    if "AlignedQuarter" not in df.columns:
        df["AlignedQuarter"] = df["Quarter"]
    if "AlignedPeriod" not in df.columns:
        df["AlignedPeriod"] = df["AlignedYear"].astype(str) + df["AlignedQuarter"].astype(str)
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
    """Load the nested {ticker: {year: {period: markdown}}} insights."""
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
