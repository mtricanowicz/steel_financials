"""Build the canonical dashboard datasets consumed by the app and services.

Combines three sources:

* Auto (SEC XBRL company facts): primary financial statement metrics.
* Manual files (``data/manual/``): optional overlap values plus share
    repurchase / share sale history.
* Derived (here): margins, debt/liquidity rollups, and cash-flow metrics.

Outputs to ``data/generated/``:

* ``financials.json`` - one record per steelmaker / year / period, including
    reported and aligned quarterly period fields.
* ``buybacks.json``   - share repurchase and share sale history.

Where the manual sheet also carries an auto metric, a mismatch beyond a
relative tolerance is logged so sources can be reconciled.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from sec_pipeline import config
from sec_pipeline.edgar_client import EdgarClient
from sec_pipeline.xbrl import extract_financials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_data")

AUTO_METRICS = [
    "Net Sales",
    "Cost of Goods Sold",
    "Operating Income",
    "Net Income",
    "Net Income Attributable to Stockholders",
    "Earnings Per Share",
    "Long-Term Debt",
    "Operating Cash Flow",
    "Capital Expenditures",
]
MANUAL_METRICS: list[str] = []
MISMATCH_TOLERANCE = 0.02  # 2% relative difference
REMOVED_OUTPUT_COLUMNS = {
    "Operating Income",
    "Operating Margin",
    "Net Income",
    "Net Margin",
}

# One-off corrections for known SEC companyfacts mislabels.
_ALIGNED_PERIOD_OVERRIDES: dict[tuple[str, int, str], tuple[int, str]] = {
    ("NUE", 2022, "Q3"): (2022, "Q3"),
}

FINANCIALS_PATH = config.GENERATED_DIR / "financials.json"
BUYBACKS_PATH = config.GENERATED_DIR / "buybacks.json"
DIAGNOSTICS_DIR = config.GENERATED_DIR / "diagnostics"
DIAGNOSTICS_SUMMARY_CSV = DIAGNOSTICS_DIR / "coverage_summary.csv"
DIAGNOSTICS_DETAIL_CSV = DIAGNOSTICS_DIR / "coverage_detail.csv"
DIAGNOSTICS_REPORT_JSON = DIAGNOSTICS_DIR / "coverage_report.json"

MANUAL_XLSX = config.MANUAL_DIR / "steelmaker_financial_data.xlsx"
MANUAL_METRICS_CSV = config.MANUAL_DIR / "manual_metrics.csv"
REPURCHASES_CSV = config.MANUAL_DIR / "share_repurchases.csv"
SHARE_SALES_CSV = config.MANUAL_DIR / "share_sales.csv"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _normalize_quarter(value: Any) -> str:
    s = str(value).strip().upper()
    if s in {"FY", "Q1", "Q2", "Q3", "Q4"}:
        return s
    return f"Q{s}" if s.isdigit() else s


def load_manual() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load manual metrics, repurchases, and share sales.

    Prefers a single multi-sheet ``steelmaker_financial_data.xlsx`` (legacy
    shape), otherwise falls back to individual CSV files. Missing sources yield
    empty frames so the build can still run on auto data alone.
    """
    if MANUAL_XLSX.exists():
        sheets = pd.read_excel(MANUAL_XLSX, sheet_name=None)
        metrics = sheets.get("steelmaker_financials", pd.DataFrame())
        repurchases = sheets.get("share_repurchases", pd.DataFrame())
        sales = sheets.get("share_sales", pd.DataFrame())
    else:
        metrics = pd.read_csv(MANUAL_METRICS_CSV) if MANUAL_METRICS_CSV.exists() else pd.DataFrame()
        repurchases = pd.read_csv(REPURCHASES_CSV) if REPURCHASES_CSV.exists() else pd.DataFrame()
        sales = pd.read_csv(SHARE_SALES_CSV) if SHARE_SALES_CSV.exists() else pd.DataFrame()

    for frame in (metrics, repurchases, sales):
        if not frame.empty and "Quarter" in frame.columns:
            frame["Quarter"] = frame["Quarter"].apply(_normalize_quarter)
    return metrics, repurchases, sales


def load_auto(steelmakers: list[str], years: list[int], periods: list[str]) -> pd.DataFrame:
    """Fetch XBRL company facts and extract auto metrics per steelmaker."""
    client = EdgarClient()
    ciks = client.resolve_ciks(steelmakers)
    rows: list[dict[str, Any]] = []
    for steelmaker in steelmakers:
        try:
            facts = client.company_facts(ciks[steelmaker])
        except Exception as exc:  # noqa: BLE001
            log.error("Could not fetch company facts for %s: %s", steelmaker, exc)
            continue
        for rec in extract_financials(facts, years, periods):
            rec["Steelmaker"] = steelmaker
            rows.append(rec)
    return pd.DataFrame(rows)


def _scope_frame(
    df: pd.DataFrame,
    steelmakers: list[str],
    years: list[int],
    periods: list[str],
) -> pd.DataFrame:
    """Return only rows within the requested steelmaker/year/quarter scope."""
    if df.empty:
        return df
    out = df.copy()
    if "Steelmaker" in out.columns:
        out = out[out["Steelmaker"].isin(steelmakers)]
    if "Year" in out.columns:
        out = out[out["Year"].isin(years)]
    if "Quarter" in out.columns:
        out = out[out["Quarter"].isin(periods)]
    return out


# ---------------------------------------------------------------------------
# Merge and derive
# ---------------------------------------------------------------------------
def _report_mismatches(merged: pd.DataFrame) -> None:
    for metric in AUTO_METRICS:
        manual_col = f"{metric}_manual"
        if manual_col not in merged.columns:
            continue
        auto_vals = pd.to_numeric(merged[metric], errors="coerce")
        manual_vals = pd.to_numeric(merged[manual_col], errors="coerce")
        valid = auto_vals.notna() & manual_vals.notna() & (manual_vals != 0)
        rel = (auto_vals - manual_vals).abs() / manual_vals.abs()
        bad_mask = valid & (rel > MISMATCH_TOLERANCE)
        if not bad_mask.any():
            continue
        bad_rows = merged.loc[bad_mask, ["Steelmaker", "Year", "Quarter"]].copy()
        bad_rows["auto"] = auto_vals.loc[bad_mask]
        bad_rows["manual"] = manual_vals.loc[bad_mask]
        bad_rows["rel"] = rel.loc[bad_mask] * 100
        for row in bad_rows.itertuples(index=False):
            log.warning(
                "%s %s %s: %s auto=%.0f manual=%.0f (%.1f%%)",
                row.Steelmaker,
                row.Year,
                row.Quarter,
                metric,
                row.auto,
                row.manual,
                row.rel,
            )


def merge_sources(auto: pd.DataFrame, manual: pd.DataFrame) -> pd.DataFrame:
    """Merge auto and manual metrics on steelmaker/year/quarter."""
    keys = ["Steelmaker", "Year", "Quarter"]
    if manual.empty:
        merged = auto.copy()
        for m in MANUAL_METRICS:
            merged[m] = pd.NA
        return merged

    overlap = [m for m in AUTO_METRICS if m in manual.columns]
    manual = manual.rename(columns={m: f"{m}_manual" for m in overlap})
    merged = auto.merge(manual, on=keys, how="outer", suffixes=("", "_manual"))
    _report_mismatches(merged)

    for metric in overlap:
        merged[metric] = merged[metric].fillna(merged[f"{metric}_manual"])

    return merged


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Compute derived metrics using legacy formulas."""

    def align_period(report_end: Any, fiscal_year: Any, fiscal_quarter: Any) -> tuple[Any, Any, Any]:
        quarter = str(fiscal_quarter)
        if quarter == "FY":
            return fiscal_year, quarter, f"{fiscal_year}{quarter}"
        end = pd.to_datetime(report_end, errors="coerce")
        if pd.isna(end):
            return fiscal_year, quarter, f"{fiscal_year}{quarter}"
        quarter_ends = [
            pd.Timestamp(end.year - 1, 12, 31),
            pd.Timestamp(end.year, 3, 31),
            pd.Timestamp(end.year, 6, 30),
            pd.Timestamp(end.year, 9, 30),
            pd.Timestamp(end.year, 12, 31),
            pd.Timestamp(end.year + 1, 3, 31),
        ]
        nearest_end = min(quarter_ends, key=lambda quarter_end: abs((end - quarter_end).days))
        aligned_year = int(nearest_end.year)
        aligned_quarter = {
            3: "Q1",
            6: "Q2",
            9: "Q3",
            12: "Q4",
        }[int(nearest_end.month)]
        return aligned_year, aligned_quarter, f"{aligned_year}{aligned_quarter}"

    def col(name: str) -> pd.Series:
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(pd.NA, index=df.index)

    sales, costs = col("Net Sales"), col("Cost of Goods Sold")
    net_inc_attr = col("Net Income Attributable to Stockholders")
    ltd, curr_mat, cash_eq, unr_cash, r_cash, st_inv = (
        col("Long-Term Debt"),
        col("Current Maturities"),
        col("Cash & Cash Equivalents"),
        col("Unrestricted Cash"),
        col("Restricted Cash"),
        col("Short-Term Investments"),
    )
    ocf, capex = col("Operating Cash Flow"), col("Capital Expenditures").abs()

    df["Gross Income"] = sales - costs
    df["Gross Margin"] = ((df["Gross Income"] / sales) * 100).round(2)
    df["Net Margin Attributable to Stockholders"] = ((net_inc_attr / sales) * 100).round(2)
    df["Total Debt"] = ltd.fillna(0) + curr_mat.fillna(0)
    df["Cash & Cash Equivalents"] = cash_eq.combine_first(
        (unr_cash.fillna(0) + r_cash.fillna(0)).where(unr_cash.notna() | r_cash.notna())
    )
    df["Total Liquidity"] = col("Cash & Cash Equivalents") + st_inv.fillna(0)
    df["Net Debt"] = col("Total Debt") - col("Total Liquidity")
    df["Free Cash Flow"] = ocf - capex
    df["Period"] = df["Year"].astype(str) + df["Quarter"].astype(str)
    aligned = df.apply(
        lambda row: align_period(row.get("Reported End"), row.get("Year"), row.get("Quarter")),
        axis=1,
        result_type="expand",
    )
    aligned.columns = ["AlignedYear", "AlignedQuarter", "AlignedPeriod"]
    df[["AlignedYear", "AlignedQuarter", "AlignedPeriod"]] = aligned

    if {"Steelmaker", "Year", "Quarter"}.issubset(df.columns):
        for (steelmaker, fiscal_year, fiscal_quarter), (aligned_year, aligned_quarter) in _ALIGNED_PERIOD_OVERRIDES.items():
            mask = (
                (df["Steelmaker"] == steelmaker)
                & (pd.to_numeric(df["Year"], errors="coerce") == fiscal_year)
                & (df["Quarter"] == fiscal_quarter)
            )
            if not mask.any():
                continue
            df.loc[mask, "AlignedYear"] = aligned_year
            df.loc[mask, "AlignedQuarter"] = aligned_quarter
            df.loc[mask, "AlignedPeriod"] = f"{aligned_year}{aligned_quarter}"

    preferred_column_order = list(
        dict.fromkeys(
            column
            for column in [
                "Steelmaker",
                "Year",
                "Quarter",
                "Period",
                "Reported End",
                "AlignedYear",
                "AlignedQuarter",
                "AlignedPeriod",
                "Net Sales",
                "Cost of Goods Sold",
                "Gross Income",
                "Net Income Attributable to Stockholders",
                "Earnings Per Share",
                "Long-Term Debt",
                "Current Maturities",
                "Total Debt",
                "Cash & Cash Equivalents",
                "Short-Term Investments",
                "Total Liquidity",
                "Net Debt",
                "Operating Cash Flow",
                "Capital Expenditures",
                "Free Cash Flow",
            ]
        )
    )
    columns_to_drop = list(
        dict.fromkeys(
            column
            for column in [
                "Unrestricted Cash",
                "Restricted Cash",
                *REMOVED_OUTPUT_COLUMNS,
            ]
        )
    )
    preferred_column_order = [column for column in preferred_column_order if column in df.columns]
    remaining_column_order = [column for column in df.columns if column not in preferred_column_order]
    df = df[preferred_column_order + remaining_column_order].drop(columns=columns_to_drop, errors="ignore")

    return df


# ---------------------------------------------------------------------------
# Buybacks
# ---------------------------------------------------------------------------
def build_buybacks(repurchases: pd.DataFrame, sales: pd.DataFrame) -> dict[str, Any]:
    """Derive the share repurchase and share sale views."""
    out: dict[str, Any] = {"repurchases": [], "sales": []}
    if not repurchases.empty:
        r = repurchases.copy()
        r["Shares (millions)"] = r["Shares Repurchased"] / 1_000_000
        r["Cost (millions)"] = r["Cost"] / 1_000_000
        r["Average Share Price"] = (r["Cost"] / r["Shares Repurchased"]).fillna(0)
        r["Period"] = r["Year"].astype(str) + r["Quarter"].astype(str)
        out["repurchases"] = _records(r)
    if not sales.empty:
        s = sales.copy()
        s["Shares (millions)"] = s["Shares Sold"] / 1_000_000
        s["Proceeds (millions)"] = s["Proceeds"] / 1_000_000
        s["Average Share Price"] = (s["Proceeds"] / s["Shares Sold"]).fillna(0)
        s["Period"] = s["Year"].astype(str) + s["Quarter"].astype(str)
        out["sales"] = _records(s)
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a frame to JSON-safe records (NaN -> None)."""
    return df.astype(object).where(pd.notna(df), None).to_dict(orient="records")


def _write(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    log.info("Wrote %s", path)


def _build_coverage_diagnostics(
    merged: pd.DataFrame,
    auto: pd.DataFrame,
    manual: pd.DataFrame,
    steelmakers: list[str],
    years: list[int],
    periods: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build coverage diagnostics for the requested steelmaker/year/period slice."""
    key_cols = ["Steelmaker", "Year", "Quarter"]
    metric_sources: dict[str, str] = {
        **{metric: "auto_xbrl" for metric in AUTO_METRICS},
        **{metric: "manual_only" for metric in MANUAL_METRICS},
    }
    for metric in (
        "Current Maturities",
        "Cash & Cash Equivalents",
        "Short-Term Investments",
    ):
        metric_sources.setdefault(metric, "auto_xbrl")

    metrics = [m for m in metric_sources if m in merged.columns]
    expected_keys = [
        (steelmaker, year, period)
        for steelmaker in steelmakers
        for year in years
        for period in periods
    ]

    merged_idx = merged.set_index(key_cols) if not merged.empty else pd.DataFrame(columns=metrics)
    auto_keys = set(auto[key_cols].itertuples(index=False, name=None)) if not auto.empty else set()
    manual_keys = set(manual[key_cols].itertuples(index=False, name=None)) if not manual.empty else set()

    if config.DIAGNOSTICS_EXCLUDE_FUTURE_PERIODS:
        quarter_rank = {q: i for i, q in enumerate(config.QUARTERS)}

        def _sort_key(key: tuple[str, int, str]) -> tuple[int, int]:
            return (key[1], quarter_rank.get(key[2], 99))

        available_keys = set(auto_keys) | set(manual_keys)
        if not merged.empty:
            available_keys |= set(merged[key_cols].itertuples(index=False, name=None))

        latest_by_steelmaker: dict[str, tuple[int, int]] = {}
        for steelmaker, year, quarter in available_keys:
            if steelmaker not in steelmakers or year not in years or quarter not in periods:
                continue
            key_rank = _sort_key((steelmaker, year, quarter))
            if key_rank > latest_by_steelmaker.get(steelmaker, (-1, -1)):
                latest_by_steelmaker[steelmaker] = key_rank

        expected_keys = [
            key
            for key in expected_keys
            if key[0] not in latest_by_steelmaker or _sort_key(key) <= latest_by_steelmaker[key[0]]
        ]

    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for steelmaker in steelmakers:
        steelmaker_expected = [key for key in expected_keys if key[0] == steelmaker]
        expected_count = len(steelmaker_expected)

        for metric in metrics:
            source_type = metric_sources.get(metric, "unknown")
            populated = 0
            missing = 0

            for key in steelmaker_expected:
                value = pd.NA
                if key in merged_idx.index and metric in merged_idx.columns:
                    value = merged_idx.at[key, metric]

                if pd.notna(value):
                    populated += 1
                    continue

                missing += 1
                if source_type == "manual_only":
                    reason = "NO_MANUAL_ROW" if key not in manual_keys else "NO_MANUAL_VALUE"
                elif source_type == "auto_xbrl":
                    reason = "NO_AUTO_ROW" if key not in auto_keys else "NO_AUTO_VALUE"
                else:
                    reason = "MISSING_VALUE"

                detail_rows.append(
                    {
                        "steelmaker": key[0],
                        "year": key[1],
                        "quarter": key[2],
                        "metric": metric,
                        "source_type": source_type,
                        "reason_code": reason,
                    }
                )

            coverage_pct = round((populated / expected_count) * 100, 1) if expected_count else 0.0
            summary_rows.append(
                {
                    "steelmaker": steelmaker,
                    "metric": metric,
                    "source_type": source_type,
                    "expected_periods": expected_count,
                    "populated_periods": populated,
                    "missing_periods": missing,
                    "coverage_pct": coverage_pct,
                }
            )

    summary_df = pd.DataFrame(
        summary_rows,
        columns=[
            "steelmaker",
            "metric",
            "source_type",
            "expected_periods",
            "populated_periods",
            "missing_periods",
            "coverage_pct",
        ],
    )
    detail_df = pd.DataFrame(
        detail_rows,
        columns=[
            "steelmaker",
            "year",
            "quarter",
            "metric",
            "source_type",
            "reason_code",
        ],
    )
    if not summary_df.empty:
        summary_df = summary_df.sort_values(["steelmaker", "source_type", "metric"])
    if not detail_df.empty:
        detail_df = detail_df.sort_values(["steelmaker", "metric", "year", "quarter"])

    reason_counts = detail_df["reason_code"].value_counts().to_dict() if not detail_df.empty else {}
    report = {
        "requested_steelmakers": steelmakers,
        "requested_years": years,
        "requested_periods": periods,
        "exclude_future_periods": config.DIAGNOSTICS_EXCLUDE_FUTURE_PERIODS,
        "summary_rows": int(len(summary_df)),
        "detail_rows": int(len(detail_df)),
        "reason_counts": reason_counts,
    }
    return summary_df, detail_df, report


def _write_coverage_diagnostics(
    merged: pd.DataFrame,
    auto: pd.DataFrame,
    manual: pd.DataFrame,
    steelmakers: list[str],
    years: list[int],
    periods: list[str],
) -> None:
    """Write diagnostics tables and report under data/generated/diagnostics."""
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    summary_df, detail_df, report = _build_coverage_diagnostics(
        merged=merged,
        auto=auto,
        manual=manual,
        steelmakers=steelmakers,
        years=years,
        periods=periods,
    )
    summary_df.to_csv(DIAGNOSTICS_SUMMARY_CSV, index=False)
    detail_df.to_csv(DIAGNOSTICS_DETAIL_CSV, index=False)
    DIAGNOSTICS_REPORT_JSON.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Wrote %s", DIAGNOSTICS_SUMMARY_CSV)
    log.info("Wrote %s", DIAGNOSTICS_DETAIL_CSV)
    log.info("Wrote %s", DIAGNOSTICS_REPORT_JSON)


def _load_existing_financials() -> pd.DataFrame:
    if not FINANCIALS_PATH.exists():
        return pd.DataFrame()
    df = pd.DataFrame(json.loads(FINANCIALS_PATH.read_text(encoding="utf-8")))
    if df.empty:
        return df
    if "Period" not in df.columns:
        df["Period"] = df["Year"].astype(str) + df["Quarter"].astype(str)
    df = df.drop(columns=[c for c in REMOVED_OUTPUT_COLUMNS if c in df.columns], errors="ignore")
    return df


def _merge_financials(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new
    if new.empty:
        return existing

    key = ["Steelmaker", "Year", "Quarter"]
    new = new.drop_duplicates(subset=key, keep="last")
    existing = existing.copy()
    existing = existing[~existing.set_index(key).index.isin(new.set_index(key).index)]
    merged = pd.concat([existing, new], ignore_index=True, sort=False)
    if "Period" not in merged.columns:
        merged["Period"] = merged["Year"].astype(str) + merged["Quarter"].astype(str)
    merged = merged.drop(columns=[c for c in REMOVED_OUTPUT_COLUMNS if c in merged.columns], errors="ignore")
    return merged.sort_values(["Steelmaker", "Year", "Quarter"])


def build(
    steelmakers: list[str],
    years: list[int],
    periods: list[str],
    overwrite: bool = False,
    share_data: bool = False,
) -> None:
    auto = load_auto(steelmakers, years, periods)
    manual_metrics, repurchases, sales = load_manual()
    repurchases_full = repurchases.copy()
    sales_full = sales.copy()

    manual_metrics = _scope_frame(manual_metrics, steelmakers, years, periods)

    merged = merge_sources(auto, manual_metrics)
    merged = add_derived(merged)

    drop = [c for c in merged.columns if c.endswith("_manual")]
    merged = merged.drop(columns=drop).sort_values(["Steelmaker", "Year", "Quarter"])

    merged = _scope_frame(merged, steelmakers, years, periods)

    _write_coverage_diagnostics(
        merged=merged,
        auto=auto,
        manual=manual_metrics,
        steelmakers=steelmakers,
        years=years,
        periods=periods,
    )

    if not overwrite:
        existing_financials = _load_existing_financials()
        merged = _merge_financials(existing_financials, merged)

    merged = merged.drop(columns=[c for c in REMOVED_OUTPUT_COLUMNS if c in merged.columns], errors="ignore")

    _write(FINANCIALS_PATH, _records(merged))

    if share_data:
        buybacks = build_buybacks(repurchases_full, sales_full)
        _write(BUYBACKS_PATH, buybacks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the dashboard datasets.")
    parser.add_argument("--steelmakers", nargs="+", default=["NUE", "STLD", "CLF", "CMC"])
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--periods", nargs="+", default=["Q1", "Q2", "Q3", "Q4", "FY"])
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing generated outputs instead of merging with existing data.")
    parser.add_argument(
        "--share-data",
        action="store_true",
        help="Optionally write full static share repurchase/sale history from manual files (unscoped). If omitted, existing buybacks.json is left unchanged.",
    )
    args = parser.parse_args()
    build(
        args.steelmakers,
        args.years,
        args.periods,
        overwrite=args.overwrite,
        share_data=args.share_data,
    )


if __name__ == "__main__":
    main()
