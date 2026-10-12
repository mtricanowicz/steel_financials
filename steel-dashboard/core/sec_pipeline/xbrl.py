"""Extract auto-sourceable financial metrics from SEC XBRL company facts.

The extractor covers income statement, leverage/liquidity, and cash flow fields
used by the dashboard. It also captures representative SEC period-end metadata
so quarterly peer views can align companies by reporting timeframe while still
preserving each issuer's true fiscal period.

Flow metrics use duration contexts, with YTD-aware handling for metrics commonly
reported cumulatively in Q2/Q3. Most Q4 flows are derived as ``FY - (Q1 + Q2 +
Q3)``. Instant metrics use period-end contexts. Fact selection prefers matching
SEC ``fp`` values when available and can fall back to looser matches when strict
fiscal labels miss, but never to contexts outside the target fiscal dates.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from functools import lru_cache
import logging
from typing import Any

from . import config

log = logging.getLogger("xbrl")

# Candidate us-gaap tags per metric, tried in priority order.
DURATION_METRICS: dict[str, list[str]] = {
    "Net Sales": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueServicesNet",
        "SalesRevenueNet",
        "Revenues",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "Cost of Goods Sold": [
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfRevenue",
    ],
    "Operating Income": [
        "OperatingIncomeLoss",
    ],
    "Net Income": [
        "ProfitLoss",
        "NetIncomeLoss",
        "IncomeLossFromContinuingOperations",
    ],
    "Net Income Attributable to Stockholders": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLossAttributableToParent",
    ],
    "Earnings Per Share": [
        "EarningsPerShareBasic",
        "EarningsPerShareDiluted",
        "EarningsPerShareBasicAndDiluted",
    ],
    "Operating Cash Flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "Capital Expenditures": [
        "CapitalExpendituresIncurredButNotYetPaid",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    ],
    "Interest Expense": [
        "InterestRevenueExpenseNet",
        "InterestExpenseNonoperating",
        "InterestExpense",
        "InterestExpenseOperating",
        "InterestCostsIncurred",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNonoperatingNet",
        "InterestIncomeExpenseNet",
        "InterestAndOtherFinancingCosts",
        "InterestAndOtherFinancingCostsNonOperating",
        "InterestCosts",
        "InterestExpenseNonOperatingNet",
        "InterestExpenseDebtNonOperating",
    ],
    "Tax Expense": [
        "IncomeTaxExpenseBenefit",
    ],
    "Losses & Impairment of Assets": [
        "AssetImpairmentCharges",
        "LossOnImpairmentOfAssets",
    ],
    "Depreciation & Amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
    ],
    "Depreciation": [
        "Depreciation",
    ],
    "Amortization": [
        "AmortizationOfIntangibleAssets",
    ],
}

INSTANT_METRICS: dict[str, list[str]] = {
    "Long-Term Debt": [
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ],
    "Current Maturities": [
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "LongTermDebtCurrent",
        "DebtCurrent",
        "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
        "CapitalLeaseObligationsCurrent",
        "OtherLongTermDebtCurrent",
        "ShortTermBorrowings",
    ],
    "Cash & Cash Equivalents": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "Unrestricted Cash": [
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "Restricted Cash": [
        "RestrictedCashAndCashEquivalentsCurrent",
        "RestrictedCashAndCashEquivalentsNoncurrent",
        "RestrictedCashCurrent",
        "RestrictedCashNoncurrent",
        "RestrictedCashAndCashEquivalentsAtCarryingValue",
        "RestrictedCash",
        "RestrictedCashEquivalentsAtCarryingValue",
    ],
    "Short-Term Investments": [
        "ShortTermInvestments",
    ],
}

ALL_METRICS = tuple(DURATION_METRICS) + tuple(INSTANT_METRICS)

_QUARTER_END_MONTH = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12, "FY": 12}
YTD_DERIVED_METRICS = {
    "Operating Cash Flow",
    "Capital Expenditures",
}
PERIOD_ONLY_DURATION_METRICS = {
    "Losses & Impairment of Assets",
    "Depreciation & Amortization",
    "Depreciation",
    "Amortization",
}
NON_ADDITIVE_DURATION_METRICS = {"Earnings Per Share"}
METRIC_UNIT_CANDIDATES: dict[str, list[str]] = {
    "Earnings Per Share": ["USD/shares", "USD/share", "pure"],
}
EPS_SHARE_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]
EPS_SHARE_UNIT_CANDIDATES = ["shares"]
CAPEX_BROAD_TAGS = [
    "CapitalExpendituresIncurredButNotYetPaid",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
]
CAPEX_COMPONENT_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
]
CAPEX_RECONCILE_TOLERANCE = 0.05
ATTRIBUTABLE_NET_INCOME_METRIC = "Net Income Attributable to Stockholders"
PERIOD_END_TOLERANCE_DAYS = 7
INTEREST_TAGS_BY_CIK = {
    config.STEELMAKER_CIK_FALLBACK["NUE"].lstrip("0"): ["InterestRevenueExpenseNet"],
    config.STEELMAKER_CIK_FALLBACK["CLF"].lstrip("0"): ["InterestIncomeExpenseNonoperatingNet"],
    config.STEELMAKER_CIK_FALLBACK["CMC"].lstrip("0"): [
        "InterestExpense",
        "InterestExpenseOperating",
    ],
    config.STEELMAKER_CIK_FALLBACK["STLD"].lstrip("0"): [
        "InterestExpenseNonoperating",
        "InterestExpense",
    ],
}
INTEREST_INCOME_POSITIVE_TAGS = {
    "InterestRevenueExpenseNet",
    "InterestIncomeExpenseNonoperatingNet",
    "InterestIncomeExpenseNet",
}
YTD_METRICS_BY_CIK = {
    config.STEELMAKER_CIK_FALLBACK["NUE"].lstrip("0"): {
        "Losses & Impairment of Assets",
        "Depreciation",
    },
    config.STEELMAKER_CIK_FALLBACK["CMC"].lstrip("0"): {
        "Depreciation & Amortization",
    },
}


def _facts_for_tag(
    facts: dict[str, Any],
    tag: str,
    unit_candidates: list[str] | None = None,
) -> list[dict[str, Any]]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node:
        return []
    units = node.get("units", {})
    if unit_candidates is None:
        unit_candidates = ["USD"]
    out: list[dict[str, Any]] = []
    for unit in unit_candidates:
        out.extend(units.get(unit, []))
    return out


def _fact_sort_key(fact: dict[str, Any]) -> tuple[datetime, datetime, str, str]:
    end = _parse(fact.get("end")) or datetime.min
    start = _parse(fact.get("start")) or datetime.min
    filed = str(fact.get("filed", ""))
    accn = str(fact.get("accn", ""))
    return (end, start, filed, accn)


def _latest_fact(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the latest fact, preferring current-period contexts over comparatives."""
    if not candidates:
        return None
    return max(candidates, key=_fact_sort_key)


def _latest_value(candidates: list[dict[str, Any]]) -> float | None:
    """Pick the latest fact, preferring current-period contexts over comparatives."""
    best = _latest_fact(candidates)
    if best is None:
        return None
    return float(best["val"])


@lru_cache(maxsize=8192)
def _parse(d: str | None) -> datetime | None:
    try:
        return datetime.strptime(d, "%Y-%m-%d") if d else None
    except ValueError:
        return None


def _duration_days(fact: dict[str, Any]) -> int | None:
    start, end = _parse(fact.get("start")), _parse(fact.get("end"))
    if start and end:
        return (end - start).days
    return None


def _fact_fiscal_year(fact: dict[str, Any]) -> int | None:
    """Return SEC fiscal year (fy) as int when available."""
    fy = fact.get("fy")
    if fy is None:
        return None
    try:
        return int(fy)
    except (TypeError, ValueError):
        return None


def _expected_period_end(year: int, end_month: int) -> datetime:
    day = calendar.monthrange(year, end_month)[1]
    return datetime(year, end_month, day)


def _end_matches_expected_period(
    end: datetime,
    year: int,
    end_month: int,
    tolerance_days: int = PERIOD_END_TOLERANCE_DAYS,
) -> bool:
    """Return True when an SEC period end aligns with a logical period end.

    Handles 52/53-week reporters that may end a quarter a few days before or
    after the calendar month-end (for example 2023-07-01 for a logical Q2).
    """
    expected = _expected_period_end(year, end_month)
    return abs((end - expected).days) <= tolerance_days


def _is_fiscal_match(fact: dict[str, Any], year: int, period: str) -> bool:
    """Fiscal labels rank facts only after actual dates have been validated."""
    return _fact_fiscal_year(fact) == year and str(fact.get("fp", "")) == period


def _fiscal_year_end_month(facts: dict[str, Any], year: int) -> int:
    cik = str(facts.get("cik", "")).lstrip("0")
    if cik == config.STEELMAKER_CIK_FALLBACK["CMC"].lstrip("0"):
        return 8
    if cik:
        return 12
    # Small fixtures and non-CIK payloads can establish their calendar from FY.
    annual = [
        fact
        for tags in DURATION_METRICS.values()
        for tag in tags
        for fact in _facts_for_tag(facts, tag)
        if _fact_fiscal_year(fact) == year
        and str(fact.get("fp", "")) == "FY"
        and 350 <= (_duration_days(fact) or 0) <= 390
    ]
    latest = _latest_fact(annual)
    end = _parse(latest.get("end")) if latest else None
    if end is None:
        return 12
    if end.month == 1 and end.day <= PERIOD_END_TOLERANCE_DAYS:
        return 12
    return end.month


def _fiscal_period_end(year: int, period: str, fiscal_year_end_month: int) -> datetime:
    offset = {"Q1": -9, "Q2": -6, "Q3": -3, "Q4": 0, "FY": 0}[period]
    month_index = year * 12 + fiscal_year_end_month - 1 + offset
    target_year, month_zero = divmod(month_index, 12)
    return _expected_period_end(target_year, month_zero + 1)


def _duration_context_matches(
    fact: dict[str, Any],
    target_end: datetime,
    months: int,
) -> bool:
    start, end = _parse(fact.get("start")), _parse(fact.get("end"))
    if start is None or end is None:
        return False
    month_index = target_end.year * 12 + target_end.month - months
    start_year, start_month_zero = divmod(month_index, 12)
    target_start = datetime(start_year, start_month_zero + 1, 1)
    return (
        _end_matches_expected_period(end, target_end.year, target_end.month)
        and abs((start - target_start).days) <= PERIOD_END_TOLERANCE_DAYS
    )


def _pick_duration_fact_window(
    facts_list: list[dict[str, Any]],
    year: int,
    end_month: int,
    min_days: int,
    max_days: int,
    period: str | None = None,
    *,
    enable_fp_fallback: bool,
    fiscal_year_end_month: int = 12,
) -> dict[str, Any] | None:
    """Pick a duration fact matching year/month and day-count window."""
    exact_match: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    target_end = (
        _fiscal_period_end(year, period, fiscal_year_end_month)
        if period else _expected_period_end(year, end_month)
    )
    months = 12 if max_days >= 350 else 9 if max_days >= 240 else 6 if max_days >= 150 else 3
    for fact in facts_list:
        end = _parse(fact.get("end"))
        days = _duration_days(fact)
        if not end or days is None:
            continue
        if not (min_days <= days <= max_days):
            continue
        if not _duration_context_matches(fact, target_end, months):
            continue
        is_fiscal = _is_fiscal_match(fact, year, period or "FY")
        if is_fiscal:
            exact_match.append(fact)
        elif enable_fp_fallback:
            fallback.append(fact)

    selected = _latest_fact(exact_match)
    if selected is not None:
        return selected
    if enable_fp_fallback:
        return _latest_fact(fallback)

    return None


def _pick_duration_window(
    facts_list: list[dict[str, Any]],
    year: int,
    end_month: int,
    min_days: int,
    max_days: int,
    period: str | None = None,
    *,
    enable_fp_fallback: bool,
    fiscal_year_end_month: int = 12,
) -> float | None:
    fact = _pick_duration_fact_window(
        facts_list, year, end_month, min_days, max_days, period,
        enable_fp_fallback=enable_fp_fallback,
        fiscal_year_end_month=fiscal_year_end_month,
    )
    return float(fact["val"]) if fact is not None else None


def _pick_duration(
    facts_list: list[dict[str, Any]],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
    fiscal_year_end_month: int = 12,
) -> float | None:
    """Pick the flow value for a year/period from duration facts."""
    end_month = _QUARTER_END_MONTH[period]
    target_len = (350, 390) if period == "FY" else (70, 120)
    return _pick_duration_window(
        facts_list,
        year,
        end_month,
        target_len[0],
        target_len[1],
        period=period,
        enable_fp_fallback=enable_fp_fallback,
        fiscal_year_end_month=fiscal_year_end_month,
    )


def _duration_range_for_period(period: str) -> tuple[int, int]:
    if period == "Q1":
        return (70, 120)
    if period == "Q2":
        return (130, 220)
    if period == "Q3":
        return (220, 320)
    return (350, 390)


def _pick_instant(
    facts_list: list[dict[str, Any]],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
    fiscal_year_end_month: int = 12,
) -> float | None:
    """Pick the balance value for a year/period from instant facts."""
    target_end = _fiscal_period_end(year, period, fiscal_year_end_month)
    exact_match: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for fact in facts_list:
        end = _parse(fact.get("end"))
        if not end:
            continue

        if not _end_matches_expected_period(end, target_end.year, target_end.month):
            continue
        is_fiscal = _is_fiscal_match(fact, year, period)
        if is_fiscal:
            exact_match.append(fact)
        elif enable_fp_fallback:
            fallback.append(fact)

    value = _latest_value(exact_match)
    if value is not None:
        return value
    if enable_fp_fallback:
        value = _latest_value(fallback)
        if value is not None:
            return value

    return None


def _most_common_end(candidates: list[dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    best_fact_by_end: dict[str, dict[str, Any]] = {}
    for fact in candidates:
        end = fact.get("end")
        if not end:
            continue
        counts[end] = counts.get(end, 0) + 1
        best_fact = best_fact_by_end.get(end)
        if best_fact is None or _fact_sort_key(fact) > _fact_sort_key(best_fact):
            best_fact_by_end[end] = fact
    if not counts:
        return None
    return max(
        counts,
        key=lambda end: (counts[end], _parse(end) or datetime.min, _fact_sort_key(best_fact_by_end[end])),
    )


def extract_period_end(
    facts: dict[str, Any],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool = config.XBRL_ENABLE_FP_FALLBACK,
) -> str | None:
    """Return a representative SEC period end date for one fiscal year/period."""
    exact_fp: list[dict[str, Any]] = []
    exact_date_window: list[dict[str, Any]] = []
    fy_duration_fallback: list[dict[str, Any]] = []
    fiscal_year_ends: list[dict[str, Any]] = []
    fiscal_month = _fiscal_year_end_month(facts, year)
    target_end = _fiscal_period_end(year, period, fiscal_month)
    annual_end = _fiscal_period_end(year, "FY", fiscal_month)
    min_days, max_days = _duration_range_for_period(period)

    for metric, tags in DURATION_METRICS.items():
        unit_candidates = METRIC_UNIT_CANDIDATES.get(metric)
        for tag in tags:
            tag_exact_fp: list[dict[str, Any]] = []
            tag_fiscal_year_ends: list[dict[str, Any]] = []
            for fact in _facts_for_tag(facts, tag, unit_candidates):
                end = _parse(fact.get("end"))
                if not end:
                    continue
                fp = str(fact.get("fp", ""))
                fy = _fact_fiscal_year(fact)
                days = _duration_days(fact)
                if (
                    fy == year and fp == "FY"
                    and days is not None and 350 <= days <= 390
                    and abs((end - annual_end).days) <= PERIOD_END_TOLERANCE_DAYS
                ):
                    tag_fiscal_year_ends.append(fact)
                if abs((end - target_end).days) > PERIOD_END_TOLERANCE_DAYS:
                    continue
                if fy == year and fp == period:
                    if period == "FY" and (days is None or not 350 <= days <= 390):
                        continue
                    tag_exact_fp.append(fact)
                    continue
                if abs((end - target_end).days) <= PERIOD_END_TOLERANCE_DAYS:
                    exact_date_window.append(fact)
                    continue
                if (
                    enable_fp_fallback
                    and period in {"Q2", "Q3", "Q4"}
                    and fy == year
                    and fp == "FY"
                    and days is not None
                    and min_days <= days <= max_days
                ):
                    fy_duration_fallback.append(fact)
            # SEC fy/fp also label comparative contexts in the same filing.
            # Give each tag one current-period vote, not one per historical fact.
            latest = _latest_fact(tag_exact_fp)
            if latest is not None:
                exact_fp.append(latest)
            latest = _latest_fact(tag_fiscal_year_ends)
            if latest is not None:
                fiscal_year_ends.append(latest)

    for metric in INSTANT_METRICS:
        for tag in INSTANT_METRICS[metric]:
            tag_exact_fp = []
            for fact in _facts_for_tag(facts, tag):
                end = _parse(fact.get("end"))
                if not end:
                    continue
                fp = str(fact.get("fp", ""))
                fy = _fact_fiscal_year(fact)
                if period == "FY":
                    continue
                if abs((end - target_end).days) > PERIOD_END_TOLERANCE_DAYS:
                    continue
                if fy == year and fp == period:
                    tag_exact_fp.append(fact)
                    continue
                if abs((end - target_end).days) <= PERIOD_END_TOLERANCE_DAYS:
                    exact_date_window.append(fact)
            latest = _latest_fact(tag_exact_fp)
            if latest is not None:
                exact_fp.append(latest)

    if period == "Q4" and not exact_fp:
        return _most_common_end(fiscal_year_ends) or _most_common_end(exact_date_window)
    return (
        _most_common_end(exact_fp)
        or (_most_common_end(fiscal_year_ends) if period == "FY" else None)
        or _most_common_end(exact_date_window)
        or _most_common_end(fy_duration_fallback)
    )


def _extract_ytd_from_tags(
    facts: dict[str, Any],
    tags: list[str],
    year: int,
    period: str,
    unit_candidates: list[str] | None = None,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Extract duration metric values when Q2/Q3 may be filed as YTD."""
    fiscal_month = _fiscal_year_end_month(facts, year)
    if period in {"Q1", "Q2", "Q3", "Q4"}:
        for tag in tags:
            direct = _pick_duration(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if direct is not None:
                return direct

    if period == "Q1":
        for tag in tags:
            q1_ytd = _pick_duration_window(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                3,
                70,
                120,
                period="Q1",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if q1_ytd is not None:
                return q1_ytd
        return None

    if period == "Q2":
        for tag in tags:
            facts_list = _facts_for_tag(facts, tag, unit_candidates)
            q2_ytd = _pick_duration_fact_window(
                facts_list,
                year,
                6,
                150,
                220,
                period="Q2",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            q1_ytd = _pick_duration_fact_window(
                facts_list,
                year,
                3,
                70,
                120,
                period="Q1",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if q2_ytd is not None and q1_ytd is not None:
                if q2_ytd["start"] != q1_ytd["start"]:
                    log.warning("Incompatible YTD starts for %s %s Q2", tag, year)
                    continue
                return float(q2_ytd["val"]) - float(q1_ytd["val"])
        return None

    if period == "Q3":
        for tag in tags:
            facts_list = _facts_for_tag(facts, tag, unit_candidates)
            q3_ytd = _pick_duration_fact_window(
                facts_list,
                year,
                9,
                240,
                320,
                period="Q3",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            q2_ytd = _pick_duration_fact_window(
                facts_list,
                year,
                6,
                150,
                220,
                period="Q2",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if q3_ytd is not None and q2_ytd is not None:
                if q3_ytd["start"] != q2_ytd["start"]:
                    log.warning("Incompatible YTD starts for %s %s Q3", tag, year)
                    continue
                return float(q3_ytd["val"]) - float(q2_ytd["val"])
        return None

    if period == "Q4":
        for tag in tags:
            facts_list = _facts_for_tag(facts, tag, unit_candidates)
            fy = _pick_duration_fact_window(
                facts_list,
                year,
                12,
                350,
                390,
                period="FY",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            q3_ytd = _pick_duration_fact_window(
                facts_list,
                year,
                9,
                240,
                320,
                period="Q3",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if fy is not None and q3_ytd is not None:
                if fy["start"] != q3_ytd["start"]:
                    log.warning("Incompatible YTD starts for %s %s Q4", tag, year)
                    continue
                return float(fy["val"]) - float(q3_ytd["val"])

        fy = _extract_ytd_from_tags(
            facts,
            tags,
            year,
            "FY",
            unit_candidates,
            enable_fp_fallback=enable_fp_fallback,
        )
        parts = [
            _extract_ytd_from_tags(
                facts,
                tags,
                year,
                q,
                unit_candidates,
                enable_fp_fallback=enable_fp_fallback,
            )
            for q in ("Q1", "Q2", "Q3")
        ]
        if fy is None or any(p is None for p in parts):
            return None
        return fy - sum(parts)  # type: ignore[arg-type]

    if period == "FY":
        for tag in tags:
            fy = _pick_duration(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                "FY",
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if fy is not None:
                return fy
        return None

    return None


def _extract_ytd_metric(
    facts: dict[str, Any],
    metric: str,
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Extract named duration metrics that may be reported YTD in Q2/Q3."""
    return _extract_ytd_from_tags(
        facts,
        DURATION_METRICS[metric],
        year,
        period,
        unit_candidates=METRIC_UNIT_CANDIDATES.get(metric),
        enable_fp_fallback=enable_fp_fallback,
    )


def _extract_capex_metric(
    facts: dict[str, Any],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Extract CapEx with broad-tag precedence and component-sum fallback."""
    broad_val = _extract_ytd_from_tags(
        facts,
        CAPEX_BROAD_TAGS,
        year,
        period,
        enable_fp_fallback=enable_fp_fallback,
    )

    components = [
        _extract_ytd_from_tags(
            facts,
            [tag],
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
        )
        for tag in CAPEX_COMPONENT_TAGS
    ]
    component_vals = [value for value in components if value is not None]
    component_sum = sum(component_vals) if component_vals else None

    if broad_val is not None:
        if component_sum is None:
            return broad_val

        base = max(abs(broad_val), abs(component_sum), 1.0)
        rel_diff = abs(broad_val - component_sum) / base
        if rel_diff <= CAPEX_RECONCILE_TOLERANCE:
            return broad_val

        log.debug(
            "CapEx broad/component mismatch year=%s period=%s broad=%s component_sum=%s rel_diff=%.3f",
            year,
            period,
            broad_val,
            component_sum,
            rel_diff,
        )
        return component_sum

    return component_sum


def _extract_restricted_cash_metric(
    facts: dict[str, Any],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Extract restricted cash by summing current/noncurrent components safely."""
    current_tags = [
        "RestrictedCashAndCashEquivalentsCurrent",
        "RestrictedCashCurrent",
    ]
    noncurrent_tags = [
        "RestrictedCashAndCashEquivalentsNoncurrent",
        "RestrictedCashNoncurrent",
    ]

    current_val: float | None = None
    for tag in current_tags:
        current_val = _pick_instant(
            _facts_for_tag(facts, tag),
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
            fiscal_year_end_month=_fiscal_year_end_month(facts, year),
        )
        if current_val is not None:
            break

    noncurrent_val: float | None = None
    for tag in noncurrent_tags:
        noncurrent_val = _pick_instant(
            _facts_for_tag(facts, tag),
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
            fiscal_year_end_month=_fiscal_year_end_month(facts, year),
        )
        if noncurrent_val is not None:
            break

    if current_val is None and noncurrent_val is None:
        # Some filers only report a combined restricted-cash fact.
        for tag in INSTANT_METRICS["Restricted Cash"]:
            value = _pick_instant(
                _facts_for_tag(facts, tag),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=_fiscal_year_end_month(facts, year),
            )
            if value is not None:
                return value
        return None
    return (current_val or 0.0) + (noncurrent_val or 0.0)


def _extract_eps_period_fallback(
    facts: dict[str, Any],
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Derive period EPS from attributable net income and diluted shares."""
    net_income = extract_metric(
        facts,
        ATTRIBUTABLE_NET_INCOME_METRIC,
        year,
        period,
        enable_fp_fallback=enable_fp_fallback,
    )
    if net_income is None:
        return None

    shares: float | None = None
    for tag in EPS_SHARE_TAGS:
        shares = _pick_duration(
            _facts_for_tag(facts, tag, EPS_SHARE_UNIT_CANDIDATES),
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
            fiscal_year_end_month=_fiscal_year_end_month(facts, year),
        )
        if shares is not None:
            break

    if shares in (None, 0.0):
        return None
    return net_income / shares


def _extract_eps_q4_fallback(
    facts: dict[str, Any],
    year: int,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Derive Q4 EPS if direct Q4 value is not present."""
    eps_tags = DURATION_METRICS["Earnings Per Share"]
    eps_units = METRIC_UNIT_CANDIDATES.get("Earnings Per Share")

    eps_vals: dict[str, float] = {}
    for period in ("Q1", "Q2", "Q3", "FY"):
        value: float | None = None
        for tag in eps_tags:
            value = _pick_duration(
                _facts_for_tag(facts, tag, eps_units),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=_fiscal_year_end_month(facts, year),
            )
            if value is not None:
                break
        if value is None:
            return None
        eps_vals[period] = value

    shares_vals: dict[str, float] = {}
    for period in ("Q1", "Q2", "Q3", "FY"):
        value = None
        for tag in EPS_SHARE_TAGS:
            value = _pick_duration(
                _facts_for_tag(facts, tag, EPS_SHARE_UNIT_CANDIDATES),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=_fiscal_year_end_month(facts, year),
            )
            if value is not None:
                break
        if value is None:
            return None
        shares_vals[period] = value

    shares_q4 = (4.0 * shares_vals["FY"]) - shares_vals["Q1"] - shares_vals["Q2"] - shares_vals["Q3"]
    if shares_q4 <= 0:
        return None

    ni_fy = eps_vals["FY"] * shares_vals["FY"]
    ni_q1_3 = (
        eps_vals["Q1"] * shares_vals["Q1"]
        + eps_vals["Q2"] * shares_vals["Q2"]
        + eps_vals["Q3"] * shares_vals["Q3"]
    )
    eps_q4 = (ni_fy - ni_q1_3) / shares_q4
    return round(eps_q4, 2)


def _extract_metric_fallback(
    facts: dict[str, Any],
    metric: str,
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool,
) -> float | None:
    """Return metric-specific fallback values when primary tags are unavailable."""
    if metric != ATTRIBUTABLE_NET_INCOME_METRIC:
        return None
    return extract_metric(
        facts,
        "Net Income",
        year,
        period,
        enable_fp_fallback=enable_fp_fallback,
    )


def extract_metric(
    facts: dict[str, Any],
    metric: str,
    year: int,
    period: str,
    *,
    enable_fp_fallback: bool = config.XBRL_ENABLE_FP_FALLBACK,
) -> float | None:
    """Extract one metric for one year/period, deriving Q4 when needed."""
    fiscal_month = _fiscal_year_end_month(facts, year)
    if metric in INSTANT_METRICS:
        if metric == "Restricted Cash":
            return _extract_restricted_cash_metric(
                facts,
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
        if metric == "Cash & Cash Equivalents":
            for tag in INSTANT_METRICS[metric]:
                value = _pick_instant(
                    _facts_for_tag(facts, tag),
                    year,
                    period,
                    enable_fp_fallback=enable_fp_fallback,
                    fiscal_year_end_month=fiscal_month,
                )
                if value is not None:
                    return value

            unrestricted = extract_metric(
                facts,
                "Unrestricted Cash",
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
            restricted = extract_metric(
                facts,
                "Restricted Cash",
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
            if unrestricted is None and restricted is None:
                return None
            return (unrestricted or 0.0) + (restricted or 0.0)

        for tag in INSTANT_METRICS[metric]:
            value = _pick_instant(
                _facts_for_tag(facts, tag),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if value is not None:
                return value
        return None

    issuer_ytd = YTD_METRICS_BY_CIK.get(str(facts.get("cik", "")).lstrip("0"), set())
    if metric in YTD_DERIVED_METRICS or metric in issuer_ytd:
        if metric == "Capital Expenditures":
            return _extract_capex_metric(
                facts,
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
        return _extract_ytd_metric(
            facts,
            metric,
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
        )

    tags = DURATION_METRICS[metric]
    if metric == "Interest Expense":
        tags = INTEREST_TAGS_BY_CIK.get(str(facts.get("cik", "")).lstrip("0"), tags)
    unit_candidates = METRIC_UNIT_CANDIDATES.get(metric)
    if period == "Q4" and metric in PERIOD_ONLY_DURATION_METRICS:
        for tag in tags:
            value = _pick_duration(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if value is not None:
                return value
    if period != "Q4":
        for tag in tags:
            value = _pick_duration(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if metric == "Interest Expense":
                if value is not None:
                    return -value if tag in INTEREST_INCOME_POSITIVE_TAGS else value
            elif value is not None:
                return value

        if metric not in NON_ADDITIVE_DURATION_METRICS | PERIOD_ONLY_DURATION_METRICS:
            # Fallback for additive metrics when only YTD contexts are published.
            for tag in tags:
                ytd_value = _extract_ytd_from_tags(
                    facts,
                    [tag],
                    year,
                    period,
                    unit_candidates,
                    enable_fp_fallback=enable_fp_fallback,
                )
                if ytd_value is not None:
                    if metric == "Interest Expense" and tag in INTEREST_INCOME_POSITIVE_TAGS:
                        return -ytd_value
                    return ytd_value

        if metric == "Earnings Per Share":
            eps_fallback = _extract_eps_period_fallback(
                facts,
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
            if eps_fallback is not None:
                return eps_fallback

        return _extract_metric_fallback(
            facts,
            metric,
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
        )

    if metric in NON_ADDITIVE_DURATION_METRICS:
        for tag in tags:
            value = _pick_duration(
                _facts_for_tag(facts, tag, unit_candidates),
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
                fiscal_year_end_month=fiscal_month,
            )
            if value is not None:
                return value
        if metric == "Earnings Per Share" and period == "Q4":
            return _extract_eps_q4_fallback(
                facts,
                year,
                enable_fp_fallback=enable_fp_fallback,
            )
        return _extract_metric_fallback(
            facts,
            metric,
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
        )

    fy = extract_metric(
        facts,
        metric,
        year,
        "FY",
        enable_fp_fallback=enable_fp_fallback,
    )
    parts = [
        extract_metric(
            facts,
            metric,
            year,
            quarter,
            enable_fp_fallback=enable_fp_fallback,
        )
        for quarter in ("Q1", "Q2", "Q3")
    ]
    if fy is None or any(part is None for part in parts):
        return _extract_metric_fallback(
            facts,
            metric,
            year,
            period,
            enable_fp_fallback=enable_fp_fallback,
        )
    return fy - sum(parts)  # type: ignore[arg-type]


def extract_financials(
    facts: dict[str, Any],
    years: list[int],
    periods: list[str],
    *,
    enable_fp_fallback: bool = config.XBRL_ENABLE_FP_FALLBACK,
) -> list[dict[str, Any]]:
    """Return one record per year/period with auto-sourced metrics."""
    records: list[dict[str, Any]] = []
    for year in years:
        for period in periods:
            row: dict[str, Any] = {"Year": year, "Quarter": period}
            period_end = extract_period_end(
                facts,
                year,
                period,
                enable_fp_fallback=enable_fp_fallback,
            )
            if period_end is not None:
                row["Reported End"] = period_end
            has_any = False
            for metric in ALL_METRICS:
                value = extract_metric(
                    facts,
                    metric,
                    year,
                    period,
                    enable_fp_fallback=enable_fp_fallback,
                )
                row[metric] = value
                has_any = has_any or value is not None
            if has_any:
                records.append(row)
    return records
