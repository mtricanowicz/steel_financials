"""Rate-limited, cached client for the SEC EDGAR REST APIs.

Wraps the SEC submissions, company facts, and archive endpoints. All requests go
through a single throttled, on-disk-cached session so repeated pipeline runs do
not re-download unchanged data and never exceed the SEC rate limit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests_cache
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config


class _RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second
        self._lock = threading.Lock()
        self._next_allowed = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
                now = time.monotonic()
            self._next_allowed = max(self._next_allowed, now) + self._min_interval


@dataclass(frozen=True)
class Filing:
    """A single filing within a company's submission history."""

    accession: str
    form: str
    filing_date: datetime
    primary_document: str
    report_date: datetime | None = None

    @property
    def accession_nodashes(self) -> str:
        return self.accession.replace("-", "")


class EdgarClient:
    """Thin, polite wrapper over the SEC EDGAR APIs."""

    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"

    def __init__(self, user_agent: str | None = None) -> None:
        self._limiter = _RateLimiter(config.SEC_MAX_REQUESTS_PER_SECOND)
        self._session = requests_cache.CachedSession(
            cache_name=str(config.CACHE_DIR / "edgar_http_cache"),
            backend="sqlite",
            expire_after=60 * 60 * 24,  # 24h; filings are immutable, facts update slowly
        )
        self._session.headers.update(
            {
                "User-Agent": user_agent or config.SEC_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
        )

    # -- low-level ---------------------------------------------------------
    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20))
    def _get(self, url: str) -> Any:
        # Only throttle on live requests, not cache hits.
        if not getattr(self._session.cache, "contains", lambda **_: False)(url=url):
            self._limiter.wait()
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp

    def _get_json(self, url: str) -> dict[str, Any]:
        return self._get(url).json()

    # -- public API --------------------------------------------------------
    @staticmethod
    def normalize_cik(cik: str | int) -> str:
        return str(int(str(cik).lstrip("CIK").lstrip("0") or "0")).zfill(10)

    def fetch_ticker_to_cik(self) -> dict[str, str]:
        """Return an uppercase ticker -> zero-padded CIK map from SEC."""
        data = self._get_json(self.TICKER_MAP_URL)
        return {
            row["ticker"].upper(): str(row["cik_str"]).zfill(10)
            for row in data.values()
        }

    def resolve_ciks(self, tickers: list[str]) -> dict[str, str]:
        """Resolve tickers to CIKs, preferring live SEC data with a fallback."""
        try:
            live = self.fetch_ticker_to_cik()
        except Exception:
            live = {}
        out: dict[str, str] = {}
        for t in tickers:
            t = t.upper()
            out[t] = live.get(t) or config.STEELMAKER_CIK_FALLBACK.get(t, "")
        missing = [t for t, c in out.items() if not c]
        if missing:
            raise ValueError(f"Could not resolve CIK for: {missing}")
        return out

    def list_filings(self, cik: str) -> list[Filing]:
        """Return the recent filing history for a CIK as Filing objects."""
        cik10 = self.normalize_cik(cik)
        data = self._get_json(self.SUBMISSIONS_URL.format(cik=cik10))
        recent = data.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        report_dates = recent.get("reportDate", [])
        filings: list[Filing] = []
        for idx, acc in enumerate(accessions):
            try:
                form = forms[idx]
                date_str = filing_dates[idx]
                doc = primary_docs[idx]
            except IndexError:
                continue
            report_date_raw = report_dates[idx] if idx < len(report_dates) else None
            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            report_date: datetime | None = None
            try:
                if report_date_raw:
                    report_date = datetime.strptime(report_date_raw, "%Y-%m-%d")
            except (ValueError, TypeError):
                report_date = None
            filings.append(
                Filing(
                    accession=acc,
                    form=form,
                    filing_date=filing_date,
                    primary_document=doc,
                    report_date=report_date,
                )
            )
        return filings

    def filings_in_window(
        self, cik: str, start: datetime, end: datetime, forms: tuple[str, ...]
    ) -> list[Filing]:
        """Filings whose filing date falls within [start, end] and match forms."""
        return [
            f
            for f in self.list_filings(cik)
            if f.form in forms and start <= f.filing_date <= end
        ]

    def document_url(self, cik: str, filing: Filing) -> str:
        cik_int = int(self.normalize_cik(cik))
        return self.ARCHIVE_URL.format(
            cik_int=cik_int, acc=filing.accession_nodashes, doc=filing.primary_document
        )

    def fetch_document(self, cik: str, filing: Filing) -> bytes:
        """Download the primary document bytes for a filing."""
        return self._get(self.document_url(cik, filing)).content

    def company_facts(self, cik: str) -> dict[str, Any]:
        """Return the XBRL companyfacts payload for a CIK."""
        cik10 = self.normalize_cik(cik)
        return self._get_json(self.COMPANY_FACTS_URL.format(cik=cik10))
