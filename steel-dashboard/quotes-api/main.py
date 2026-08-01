"""Live stock quote microservice for the steel dashboard.

A small FastAPI app that returns the most recent daily close for the steel
tickers. Results are cached for the current trading day so the upstream provider
is queried at most once per ticker per day. This service is the only component
that needs live network access at request time; all financial data is precomputed
into static JSON by the core pipeline.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("quotes-api")

DEFAULT_TICKERS = ["NUE", "STLD", "CMC", "CLF"]
ALLOWED_ORIGINS = [origin for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin]
MARKET_TZ = ZoneInfo("America/New_York")

app = FastAPI(title="Steel Dashboard Quotes API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# date -> {ticker: quote}. Reset implicitly when the date key changes.
_cache: dict[dt.date, dict[str, "Quote"]] = {}
_lock = Lock()


class Quote(BaseModel):
    ticker: str
    price: float | None
    change: float | None = None
    change_percent: float | None = None
    as_of: str | None = None
    error: str | None = None


class QuotesResponse(BaseModel):
    quotes: list[Quote]


class History(BaseModel):
    dates: list[str]
    closes: dict[str, list[float | None]]


class HistoryResponse(BaseModel):
    history: History


def _fetch_quote(ticker: str) -> Quote:
    """Fetch the last two daily closes to derive price and day change."""
    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="1d")
        if hist.empty:
            return Quote(ticker=ticker, price=None, error="no data")
        closes = hist["Close"].dropna()
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else price
        change = price - prev
        pct = (change / prev * 100) if prev else 0.0
        as_of = closes.index[-1].date().isoformat()
        return Quote(
            ticker=ticker,
            price=round(price, 2),
            change=round(change, 2),
            change_percent=round(pct, 2),
            as_of=as_of,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Quote fetch failed for %s: %s", ticker, exc)
        return Quote(ticker=ticker, price=None, error="fetch failed")


def get_quote_cached(ticker: str) -> Quote:
    today = dt.date.today()
    with _lock:
        day_cache = _cache.setdefault(today, {})
        for key in [k for k in _cache if k != today]:
            _cache.pop(key, None)
        if ticker in day_cache:
            return day_cache[ticker]
    quote = _fetch_quote(ticker)
    if quote.price is not None:
        with _lock:
            _cache.setdefault(today, {})[ticker] = quote
    return quote


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/quotes", response_model=QuotesResponse)
def quotes(
    tickers: str = Query(default=",".join(DEFAULT_TICKERS), description="Comma-separated tickers"),
) -> QuotesResponse:
    symbols = [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
    return QuotesResponse(quotes=[get_quote_cached(symbol) for symbol in symbols])


# (today, start, symbols) -> History payload, so the provider is queried at most
# once per distinct request per trading day. A separate lock serializes the
# actual downloads because yfinance's shared cache is not concurrency-safe.
_history_cache: dict[tuple, History] = {}
_history_fetch_lock = Lock()


def _download_close(sym: str, start: str, attempts: int = 3) -> pd.Series | None:
    """Download one ticker's daily closes, retrying transient provider errors."""
    for attempt in range(attempts):
        try:
            data = yf.download(sym, start=start, interval="1d", progress=False, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("History download error for %s (try %d): %s", sym, attempt + 1, exc)
            data = None
        if data is not None and not data.empty:
            close = data["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if not close.empty:
                return close
    return None


def _fetch_history(symbols: list[str], start: str) -> History:
    """Fetch aligned daily closes for the given tickers since ``start``."""
    frames: dict[str, pd.Series] = {}
    for sym in symbols:
        close = _download_close(sym, start)
        if close is not None:
            frames[sym] = close
    if not frames:
        return History(dates=[], closes={})
    df = pd.DataFrame(frames).sort_index()
    dates = [day.date().isoformat() for day in df.index]
    closes = {
        sym: [None if pd.isna(value) else round(float(value), 4) for value in df[sym]]
        for sym in df.columns
    }
    return History(dates=dates, closes=closes)


@app.get("/history", response_model=HistoryResponse)
def history(
    tickers: str = Query(default=",".join(DEFAULT_TICKERS), description="Comma-separated tickers"),
    start: str = Query(..., description="Start date, YYYY-MM-DD"),
) -> HistoryResponse:
    symbols = [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
    key = (dt.date.today(), start, tuple(symbols))
    with _lock:
        cached = _history_cache.get(key)
        for stale in [k for k in _history_cache if k[0] != key[0]]:
            _history_cache.pop(stale, None)
    if cached is not None:
        return HistoryResponse(history=cached)

    with _history_fetch_lock:
        with _lock:
            cached = _history_cache.get(key)
        if cached is not None:
            return HistoryResponse(history=cached)
        try:
            payload = _fetch_history(symbols, start)
        except Exception as exc:  # noqa: BLE001
            log.warning("History fetch failed for %s: %s", symbols, exc)
            payload = History(dates=[], closes={})
        if payload.dates and all(sym in payload.closes for sym in symbols):
            with _lock:
                _history_cache[key] = payload
    return HistoryResponse(history=payload)


# Additions to support "live" stock quotes for crawling ticker
LIVE_QUOTE_CACHE_TTL_SECONDS = 60
_live_quote_cache: dict[str, tuple[dt.datetime, LiveQuote]] = {}
_closed_live_quote_cache: dict[str, tuple[dt.datetime, LiveQuote]] = {}


def _is_market_open(now: dt.datetime | None = None) -> bool:
    """Return True during US equities regular session (Mon-Fri, 9:30-16:00 ET)."""
    current = now.astimezone(MARKET_TZ) if now else dt.datetime.now(MARKET_TZ)
    if current.weekday() >= 5:
        return False
    session_open = current.replace(hour=9, minute=30, second=0, microsecond=0)
    session_close = current.replace(hour=16, minute=0, second=0, microsecond=0)
    return session_open <= current < session_close


def _next_market_open(now: dt.datetime | None = None) -> dt.datetime:
    """Return next market-open timestamp in ET."""
    current = now.astimezone(MARKET_TZ) if now else dt.datetime.now(MARKET_TZ)
    probe = current
    while True:
        if probe.weekday() < 5:
            open_at = probe.replace(hour=9, minute=30, second=0, microsecond=0)
            if probe < open_at:
                return open_at
        probe = (probe + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


class LiveQuote(BaseModel):
    ticker: str
    price: float | None
    change: float | None = None
    change_percent: float | None = None
    as_of: str | None = None
    market_state: str | None = None
    error: str | None = None


class LiveQuotesResponse(BaseModel):
    quotes: list[LiveQuote]


def _quote_to_live_quote(quote: Quote, market_state: str) -> LiveQuote:
    """Adapt a daily quote payload to the live-quote response model."""
    return LiveQuote(
        ticker=quote.ticker,
        price=quote.price,
        change=quote.change,
        change_percent=quote.change_percent,
        as_of=quote.as_of,
        market_state=market_state,
        error=quote.error,
    )


def get_live_quote_cached(ticker: str) -> LiveQuote:
    """Return market-hours quotes with a 60s TTL and frozen closed-session closes."""
    now = dt.datetime.now(dt.timezone.utc)
    market_open = _is_market_open(now)
    if market_open:
        with _lock:
            cached = _live_quote_cache.get(ticker)
            if cached is not None:
                fetched_at, quote = cached
                age_seconds = (now - fetched_at).total_seconds()
                if age_seconds < LIVE_QUOTE_CACHE_TTL_SECONDS:
                    return quote

        quote = _fetch_live_quote(ticker)
        if quote.price is not None:
            with _lock:
                _live_quote_cache[ticker] = (now, quote)
        return quote

    next_open_utc = _next_market_open(now).astimezone(dt.timezone.utc)
    with _lock:
        cached = _closed_live_quote_cache.get(ticker)
        if cached is not None:
            valid_until, quote = cached
            if now < valid_until:
                return quote

    quote = _quote_to_live_quote(get_quote_cached(ticker), market_state="closed")
    if quote.price is not None:
        with _lock:
            _closed_live_quote_cache[ticker] = (next_open_utc, quote)
    return quote


def _fetch_live_quote(ticker: str) -> LiveQuote:
    """Fetch the latest available intraday price and daily change."""
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=False)
        if hist.empty:
            return LiveQuote(
                ticker=ticker,
                price=None,
                market_state="open",
                error="no intraday data",
            )

        closes = hist["Close"].dropna()
        if closes.empty:
            return LiveQuote(
                ticker=ticker,
                price=None,
                market_state="open",
                error="no close data",
            )

        price = float(closes.iloc[-1])
        daily = yf.Ticker(ticker).history(period="5d", interval="1d", prepost=False)
        daily_closes = daily["Close"].dropna()

        if len(daily_closes) >= 2:
            previous_close = float(daily_closes.iloc[-2])
        elif len(daily_closes) == 1:
            previous_close = float(daily_closes.iloc[-1])
        else:
            previous_close = price

        change = price - previous_close
        change_percent = change / previous_close * 100 if previous_close else 0.0
        timestamp = closes.index[-1]
        return LiveQuote(
            ticker=ticker,
            price=round(price, 2),
            change=round(change, 2),
            change_percent=round(change_percent, 2),
            as_of=timestamp.isoformat(),
            market_state="open",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Live quote fetch failed for %s: %s", ticker, exc)

        return LiveQuote(
            ticker=ticker,
            price=None,
            market_state="open",
            error="live fetch failed",
        )


@app.get("/live-quotes", response_model=LiveQuotesResponse)
def live_quotes(
    tickers: str = Query(default=",".join(DEFAULT_TICKERS), description="Comma-separated tickers"),
) -> LiveQuotesResponse:
    symbols = [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
    return LiveQuotesResponse(quotes=[get_live_quote_cached(ticker) for ticker in symbols])
