"""Central configuration, paths, and the period model for the pipeline.

All secrets are read from the environment (see ``core/.env.example``). Nothing in
this module should contain credentials.
"""

from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

# Load core/.env if present. Never commit that file.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _env_flag(name: str, default: bool) -> bool:
    """Return a boolean environment flag with common truthy/falsey parsing."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CORE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = CORE_DIR.parent
DATA_DIR = ROOT_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"
MANUAL_DIR = DATA_DIR / "manual"
CACHE_DIR = CORE_DIR / ".cache"
RAW_DIR = DATA_DIR / "raw"
CHROMA_DIR = CACHE_DIR / "chroma"

for _d in (GENERATED_DIR, MANUAL_DIR, CACHE_DIR, RAW_DIR, CHROMA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SUMMARIES_PATH = GENERATED_DIR / "insights.json"

# ---------------------------------------------------------------------------
# Environment-driven settings
# ---------------------------------------------------------------------------
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Steel Dashboard contact@example.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "local").lower()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")
SUMMARY_TEMPERATURE = float(os.getenv("SUMMARY_TEMPERATURE", "0.55"))
SUMMARY_SEED = int(os.getenv("SUMMARY_SEED", "7"))
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
LOCAL_EMBEDDING_MODEL = os.getenv(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
XBRL_ENABLE_FP_FALLBACK = _env_flag("XBRL_ENABLE_FP_FALLBACK", True)
DIAGNOSTICS_EXCLUDE_FUTURE_PERIODS = _env_flag(
    "DIAGNOSTICS_EXCLUDE_FUTURE_PERIODS",
    True,
)

# SEC rate limit: no more than 10 requests per second.
SEC_MAX_REQUESTS_PER_SECOND = 8.0

# ---------------------------------------------------------------------------
# Steel Producers
# ---------------------------------------------------------------------------
# Fallback CIK map. The pipeline prefers the live ticker->CIK map from SEC and
# falls back to these values if the network lookup is unavailable.
STEELMAKER_CIK_FALLBACK: dict[str, str] = {
    "NUE": "0000073309",  # Nucor Corporation
    "STLD": "0001022671",  # Steel Dynamics Inc
    "CLF": "0000764065",  # Cleveland-Cliffs Inc
    "CMC": "0000022444",  # Commercial Metals Company
    "X": "0001163302",  # United States Steel Corporation
    "ATI": "0001018963",  # ATI Inc
    "CRS": "0000017843",  # Carpenter Technology Corporation
}

STEELMAKER_NAMES: dict[str, str] = {
    "NUE": "Nucor Corporation",
    "STLD": "Steel Dynamics Inc",
    "CLF": "Cleveland-Cliffs Inc",
    "CMC": "Commercial Metals Company",
    "X": "United States Steel Corporation",
    "ATI": "ATI Inc",
    "CRS": "Carpenter Technology Corporation",
}

# Filing forms relevant to a reporting period.
RELEVANT_FORMS = ("10-Q", "10-K", "8-K")

# ---------------------------------------------------------------------------
# Period model
# ---------------------------------------------------------------------------
QUARTERS = ("Q1", "Q2", "Q3", "Q4", "FY")


@dataclass(frozen=True)
class PeriodSpec:
    """A reporting period for one steelmaker, e.g. NUE 2024 Q2."""

    year: int
    period: str  # one of QUARTERS

    def __post_init__(self) -> None:
        if self.period not in QUARTERS:
            raise ValueError(f"Invalid period {self.period!r}; expected one of {QUARTERS}")

    @property
    def label(self) -> str:
        """Compact label, e.g. '2024Q2' or '2024FY'."""
        return f"{self.year}{self.period}"

    @classmethod
    def from_label(cls, label: str) -> "PeriodSpec":
        year = int(label[:4])
        return cls(year=year, period=label[4:])

    def date_window(self) -> tuple[datetime, datetime]:
        """Start and end dates that bound the filings for this period.

        This calendar-based window is a compatibility fallback. The pipeline
        uses filing report dates and issuer fiscal-year ends when available.
        Q4 and FY retain a three-month filing grace period for annual reports.
        """
        if self.period in {"Q4", "FY"}:
            start_month, end_month = 1, 12
            if self.period == "Q4":
                start_month = 10
            pad = relativedelta(months=3)
        else:
            end_month = int(self.period[-1]) * 3
            start_month = end_month - 2
            pad = relativedelta(months=1, days=1)
        start = datetime(self.year, start_month, 1)
        end = datetime(self.year, end_month, calendar.monthrange(self.year, end_month)[1]) + pad
        return start, end

    def period_end(self) -> datetime:
        """Return the actual reporting-period end, excluding filing grace."""
        end_month = 12 if self.period in {"Q4", "FY"} else int(self.period[-1]) * 3
        return datetime(self.year, end_month, calendar.monthrange(self.year, end_month)[1])


def build_periods(years: Iterable[int], periods: Iterable[str]) -> list[PeriodSpec]:
    """Cartesian product of years and periods as PeriodSpec objects."""
    return [PeriodSpec(year=y, period=p) for y in years for p in periods]
