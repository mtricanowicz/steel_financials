"""Unit tests for XBRL metric extraction behaviors."""

from __future__ import annotations

import calendar
from datetime import date

import pytest

from sec_pipeline.xbrl import extract_metric, extract_period_end


def _company_facts(tags: dict[str, dict[str, list[dict]]]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                tag: {"units": units}
                for tag, units in tags.items()
            }
        }
    }


def _use_cmc_calendar(facts: dict) -> None:
    for node in facts["facts"]["us-gaap"].values():
        for values in node["units"].values():
            for fact in values:
                for field in ("start", "end"):
                    if field not in fact:
                        continue
                    original = date.fromisoformat(fact[field])
                    year, month_zero = divmod(original.year * 12 + original.month - 5, 12)
                    day = min(original.day, calendar.monthrange(year, month_zero + 1)[1])
                    fact[field] = date(year, month_zero + 1, day).isoformat()


@pytest.mark.parametrize(
    ("metric", "tag"),
    [
        ("Losses & Impairment of Assets", "AssetImpairmentCharges"),
        ("Losses & Impairment of Assets", "LossOnImpairmentOfAssets"),
    ],
)
def test_nucor_losses_are_normalized(metric: str, tag: str) -> None:
    values = [
        {
            "start": "2024-01-01",
            "end": end,
            "form": "10-K" if period == "FY" else "10-Q",
            "fy": 2024,
            "fp": period,
            "val": value,
        }
        for end, period, value in [
            ("2024-03-31", "Q1", 40),
            ("2024-06-30", "Q2", 100),
            ("2024-09-30", "Q3", 100),
            ("2024-12-31", "FY", 180),
        ]
    ]
    facts = _company_facts({tag: {"USD": values}})
    facts["cik"] = 73309

    for period, expected in [("Q1", 40), ("Q2", 60), ("Q3", 0), ("Q4", 80), ("FY", 180)]:
        assert extract_metric(facts, metric, 2024, period) == expected

    facts = _company_facts({tag: {"USD": values[2:]}})
    facts["cik"] = "0000073309"
    assert extract_metric(facts, metric, 2024, "Q3") is None
    assert extract_metric(facts, metric, 2024, "Q4") == 80

    direct = {
        "start": "2024-07-01",
        "end": "2024-09-30",
        "form": "10-Q",
        "fy": 2024,
        "fp": "Q3",
        "val": 15,
    }
    facts = _company_facts({tag: {"USD": values + [direct]}})
    facts["cik"] = 73309
    assert extract_metric(facts, metric, 2024, "Q3") == 15
    assert extract_metric(facts, metric, 2024, "Q4") == 80


@pytest.mark.parametrize("cik", [73309, 764065, 22444, 1022671])
@pytest.mark.parametrize(
    ("metric", "tag"),
    [
        ("Depreciation", "Depreciation"),
        ("Amortization", "AmortizationOfIntangibleAssets"),
        ("Depreciation & Amortization", "DepreciationDepletionAndAmortization"),
        (
            "Depreciation & Amortization",
            "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        ),
        ("Losses & Impairment of Assets", "AssetImpairmentCharges"),
    ],
)
def test_period_expenses_preserve_standalone_values(cik: int, metric: str, tag: str) -> None:
    facts = _company_facts(
        {
            tag: {
                "USD": [
                    {
                        "start": start,
                        "end": end,
                        "form": "10-K" if period in {"Q4", "FY"} else "10-Q",
                        "fy": 2024,
                        "fp": "FY" if period == "Q4" else period,
                        "val": value,
                    }
                    for start, end, period, value in [
                        ("2024-01-01", "2024-03-31", "Q1", 40),
                        ("2024-04-01", "2024-06-30", "Q2", 60),
                        ("2024-07-01", "2024-09-30", "Q3", 0),
                        ("2024-10-01", "2024-12-31", "Q4", 80),
                        ("2024-01-01", "2024-12-31", "FY", 180),
                    ]
                ]
            }
        }
    )
    facts["cik"] = cik
    if cik == 22444:
        _use_cmc_calendar(facts)
    for period, expected in [("Q1", 40), ("Q2", 60), ("Q3", 0), ("Q4", 80), ("FY", 180)]:
        assert extract_metric(facts, metric, 2024, period) == expected

    # Q4 can still be derived when a standalone fact is absent.
    facts["facts"]["us-gaap"][tag]["units"]["USD"].pop(3)
    assert extract_metric(facts, metric, 2024, "Q4") == 80


@pytest.mark.parametrize(
    ("metric", "tag", "cik"),
    [
        ("Depreciation", "Depreciation", 764065),
        ("Amortization", "AmortizationOfIntangibleAssets", 73309),
        ("Depreciation & Amortization", "DepreciationDepletionAndAmortization", 73309),
        ("Losses & Impairment of Assets", "AssetImpairmentCharges", 764065),
        ("Losses & Impairment of Assets", "AssetImpairmentCharges", 22444),
        ("Losses & Impairment of Assets", "AssetImpairmentCharges", 1022671),
    ],
)
def test_period_only_expenses_do_not_use_ytd_fallback(metric: str, tag: str, cik: int) -> None:
    facts = _company_facts(
        {
            tag: {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": end,
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": period,
                        "val": value,
                    }
                    for end, period, value in [
                        ("2024-03-31", "Q1", 40),
                        ("2024-06-30", "Q2", 100),
                    ]
                ]
            }
        }
    )
    facts["cik"] = cik
    assert extract_metric(facts, metric, 2024, "Q2") is None


@pytest.mark.parametrize("period", ["FY", "Q4"])
def test_period_end_is_not_shifted_by_comparative_impairment_facts(period: str) -> None:
    facts = _company_facts(
        {
            tag: {
                "USD": [
                    {
                        "start": f"{year}-01-01",
                        "end": f"{year}-12-31",
                        "fy": 2023,
                        "fp": "FY",
                        "val": 100,
                    }
                    for year in years
                ]
            }
            for tag, years in [
                ("NetIncomeLoss", (2021, 2022, 2023)),
                ("Depreciation", (2021, 2022, 2023)),
                ("AssetImpairmentCharges", (2022,)),
            ]
        }
    )
    facts["cik"] = 764065
    assert extract_period_end(facts, 2023, period) == "2023-12-31"


def test_accumulated_depreciation_is_not_period_expense() -> None:
    facts = _company_facts(
        {
            "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment": {
                "USD": [
                    {
                        "end": "2024-12-31",
                        "form": "10-K",
                        "fy": 2024,
                        "fp": "FY",
                        "val": 900,
                    }
                ]
            }
        }
    )

    assert extract_metric(facts, "Depreciation & Amortization", 2024, "FY") is None


@pytest.mark.parametrize(
    ("cik", "metric", "tag"),
    [
        (73309, "Depreciation", "Depreciation"),
        (22444, "Depreciation & Amortization", "DepreciationDepletionAndAmortization"),
    ],
)
def test_issuer_cumulative_da_is_normalized(cik: int, metric: str, tag: str) -> None:
    values = [
        {
            "start": "2024-01-01",
            "end": end,
            "fy": 2024,
            "fp": period,
            "val": value,
        }
        for end, period, value in [
            ("2024-03-31", "Q1", 40),
            ("2024-06-30", "Q2", 100),
            ("2024-09-30", "Q3", 130),
            ("2024-12-31", "FY", 180),
        ]
    ]
    facts = _company_facts({tag: {"USD": values}})
    facts["cik"] = cik
    if cik == 22444:
        _use_cmc_calendar(facts)
    for period, expected in [("Q1", 40), ("Q2", 60), ("Q3", 30), ("Q4", 50), ("FY", 180)]:
        assert extract_metric(facts, metric, 2024, period) == expected
    assert extract_period_end(facts, 2024, "Q2") == (
        "2024-02-29" if cik == 22444 else "2024-06-30"
    )
    values.pop(1)
    assert extract_metric(facts, metric, 2024, "Q3") is None


def test_nucor_depreciation_and_amortization_have_distinct_duration_rules() -> None:
    facts = _company_facts(
        {
            "Depreciation": {"USD": [
                {"start": "2023-01-01", "end": "2023-03-31", "fy": 2023, "fp": "Q1", "val": 40},
                {"start": "2023-01-01", "end": "2023-07-01", "fy": 2023, "fp": "Q2", "val": 100},
            ]},
            "AmortizationOfIntangibleAssets": {"USD": [
                {"start": "2023-04-02", "end": "2023-07-01", "fy": 2023, "fp": "Q2", "val": 5},
            ]},
        }
    )
    facts["cik"] = 73309
    assert extract_metric(facts, "Depreciation", 2023, "Q2") == 60
    assert extract_metric(facts, "Amortization", 2023, "Q2") == 5


@pytest.mark.parametrize("period", ["Q2", "Q3", "Q4"])
def test_ytd_subtraction_requires_compatible_start_dates(period: str, caplog) -> None:
    facts = _company_facts(
        {
            "Depreciation": {"USD": [
                {"start": start, "end": end, "fy": 2024, "fp": fp, "val": value}
                for start, end, fp, value in [
                    ("2024-01-02", "2024-03-31", "Q1", 40),
                    ("2024-01-01", "2024-06-30", "Q2", 100),
                    ("2024-01-03", "2024-09-30", "Q3", 130),
                    ("2024-01-04", "2024-12-31", "FY", 180),
                ]
            ]},
        }
    )
    facts["cik"] = 73309
    assert extract_metric(facts, "Depreciation", 2024, period) is None
    assert "Incompatible YTD starts" in caplog.text


@pytest.mark.parametrize("fallback", [False, True])
def test_nucor_interest_rejects_wrong_year_and_quarter_labels(fallback: bool) -> None:
    facts = _company_facts(
        {
            "InterestRevenueExpenseNet": {"USD": [
                {
                    "start": start, "end": end, "fy": 2022, "fp": "Q3",
                    "val": value, "filed": filed,
                }
                for start, end, value, filed in [
                    ("2022-07-03", "2022-10-01", -42_347_000, "2022-11-01"),
                    ("2023-04-02", "2023-07-01", -4_598_000, "2023-08-01"),
                    ("2022-04-03", "2022-07-02", -57_763_000, "2023-08-01"),
                ]
            ]},
        }
    )
    facts["cik"] = 73309
    assert extract_metric(
        facts, "Interest Expense", 2022, "Q3", enable_fp_fallback=fallback
    ) == 42_347_000
    facts["facts"]["us-gaap"]["InterestRevenueExpenseNet"]["units"]["USD"].pop(0)
    assert extract_metric(
        facts, "Interest Expense", 2022, "Q3", enable_fp_fallback=fallback
    ) is None


def test_latest_restatement_is_selected_within_the_actual_period() -> None:
    facts = _company_facts(
        {
            "NetIncomeLoss": {"USD": [
                {
                    "start": "2024-04-01", "end": "2024-06-30", "fy": 2024, "fp": "Q2",
                    "filed": filed, "val": value,
                }
                for filed, value in [("2024-08-01", 10), ("2025-08-01", 12)]
            ]},
        }
    )
    assert extract_metric(facts, "Net Income", 2024, "Q2") == 12


def test_instant_facts_cannot_use_a_wrong_date_with_matching_fiscal_labels() -> None:
    facts = _company_facts(
        {
            "LongTermDebt": {"USD": [
                {"end": end, "fy": 2022, "fp": "Q3", "val": value}
                for end, value in [("2022-09-30", 100), ("2023-06-30", 999)]
            ]},
        }
    )
    assert extract_metric(facts, "Long-Term Debt", 2022, "Q3") == 100
    facts["facts"]["us-gaap"]["LongTermDebt"]["units"]["USD"].pop(0)
    assert extract_metric(facts, "Long-Term Debt", 2022, "Q3") is None


def test_x_q4_impairment_is_derived_not_selected_from_q2_in_annual_filing() -> None:
    facts = _company_facts(
        {
            "AssetImpairmentCharges": {"USD": [
                {"start": start, "end": end, "fy": 2022, "fp": period, "val": value}
                for start, end, period, value in [
                    ("2022-01-01", "2022-03-31", "Q1", 6),
                    ("2022-04-01", "2022-06-30", "Q2", 151),
                    ("2022-07-01", "2022-09-30", "Q3", 0),
                    ("2022-04-01", "2022-06-30", "FY", 151),
                    ("2022-01-01", "2022-12-31", "FY", 163),
                ]
            ]},
        }
    )
    facts["cik"] = 1163302
    assert extract_metric(facts, "Losses & Impairment of Assets", 2022, "Q4") == 6


def test_confirmed_nucor_net_income_scopes_are_preserved() -> None:
    facts = _company_facts(
        {
            tag: {"USD": [{
                "start": "2026-04-05", "end": "2026-07-04", "fy": 2026, "fp": "Q2",
                "val": value,
            }]}
            for tag, value in [
                ("ProfitLoss", 1_280_000_000),
                ("NetIncomeLoss", 1_156_000_000),
                ("NetIncomeLossAvailableToCommonStockholdersBasic", 1_152_000_000),
            ]
        }
    )
    facts["cik"] = 73309
    assert extract_metric(facts, "Net Income", 2026, "Q2") == 1_280_000_000
    assert extract_metric(facts, "Net Income Attributable to Stockholders", 2026, "Q2") == 1_156_000_000


@pytest.mark.parametrize(
    ("cik", "tag", "sign"),
    [
        (73309, "InterestRevenueExpenseNet", -1),
        (764065, "InterestIncomeExpenseNonoperatingNet", -1),
        (22444, "InterestExpense", 1),
        (1022671, "InterestExpenseNonoperating", 1),
    ],
)
def test_interest_sign_and_issuer_selection(cik: int, tag: str, sign: int) -> None:
    facts = _company_facts(
        {
            tag: {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": end,
                        "form": "10-K" if period == "FY" else "10-Q",
                        "fy": 2024,
                        "fp": period,
                        "val": value * sign,
                    }
                    for end, period, value in [
                        ("2024-03-31", "Q1", 40),
                        ("2024-06-30", "Q2", 30),
                        ("2024-09-30", "Q3", 50),
                        ("2024-12-31", "FY", 70),
                    ]
                ]
            },
            "InterestExpenseDebt": {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                        "fy": 2024,
                        "fp": "Q1",
                        "val": 999,
                    }
                ]
            },
        }
    )
    facts["cik"] = cik
    if cik == 22444:
        _use_cmc_calendar(facts)
    for period, expected in [("Q1", 40), ("Q2", -10), ("Q3", 20), ("Q4", 20), ("FY", 70)]:
        assert extract_metric(facts, "Interest Expense", 2024, period) == expected

    facts["facts"]["us-gaap"][tag]["units"]["USD"].append(
        {
            "start": "2023-12-01" if cik == 22444 else "2024-04-01",
            "end": "2024-02-29" if cik == 22444 else "2024-06-30",
            "fy": 2024,
            "fp": "Q2",
            "val": -10 * sign,
        }
    )
    assert extract_metric(facts, "Interest Expense", 2024, "Q2") == -10

    del facts["facts"]["us-gaap"][tag]
    assert extract_metric(facts, "Interest Expense", 2024, "Q1") is None


def test_zero_duration_value_is_preserved() -> None:
    facts = _company_facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q1",
                        "val": 0,
                        "filed": "2024-04-20",
                        "accn": "1",
                    }
                ]
            }
        }
    )

    assert extract_metric(facts, "Net Sales", 2024, "Q1") == 0.0


def test_fp_fallback_toggle_controls_loose_match() -> None:
    facts = _company_facts(
        {
            "NetIncomeLoss": {
                "USD": [
                    {
                        "start": "2024-04-01",
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "QX",
                        "val": 123,
                        "filed": "2024-07-20",
                        "accn": "1",
                    }
                ]
            }
        }
    )

    assert extract_metric(facts, "Net Income", 2024, "Q2", enable_fp_fallback=False) is None
    assert extract_metric(facts, "Net Income", 2024, "Q2", enable_fp_fallback=True) == 123.0


def test_q2_ytd_duration_is_derived_to_standalone() -> None:
    facts = _company_facts(
        {
            "NetIncomeLoss": {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q1",
                        "val": 40,
                        "filed": "2024-04-20",
                        "accn": "1",
                    },
                    {
                        "start": "2024-01-01",
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q2",
                        "val": 100,
                        "filed": "2024-07-20",
                        "accn": "2",
                    },
                ]
            }
        }
    )

    assert extract_metric(facts, "Net Income", 2024, "Q2") == 60.0


def test_capex_component_fallback_sums_components() -> None:
    facts = _company_facts(
        {
            "PaymentsToAcquirePropertyPlantAndEquipment": {
                "USD": [
                    {
                        "start": "2024-04-01",
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q2",
                        "val": 30,
                        "filed": "2024-07-20",
                        "accn": "1",
                    }
                ]
            },
            "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets": {
                "USD": [
                    {
                        "start": "2024-04-01",
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q2",
                        "val": 20,
                        "filed": "2024-07-20",
                        "accn": "1",
                    }
                ]
            },
        }
    )

    assert extract_metric(facts, "Capital Expenditures", 2024, "Q2") == 50.0


def test_cash_and_equivalents_falls_back_to_unrestricted_plus_restricted() -> None:
    facts = _company_facts(
        {
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [
                    {
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q2",
                        "val": 80,
                        "filed": "2024-07-20",
                        "accn": "1",
                    }
                ]
            },
            "RestrictedCashAndCashEquivalentsAtCarryingValue": {
                "USD": [
                    {
                        "end": "2024-06-30",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q2",
                        "val": 20,
                        "filed": "2024-07-20",
                        "accn": "1",
                    }
                ]
            },
        }
    )

    assert extract_metric(facts, "Cash & Cash Equivalents", 2024, "Q2") == 100.0


def test_eps_fallback_uses_net_income_and_share_count() -> None:
    facts = _company_facts(
        {
            "NetIncomeLossAttributableToParent": {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q1",
                        "val": 200,
                        "filed": "2024-04-20",
                        "accn": "1",
                    }
                ]
            },
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "shares": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-03-31",
                        "form": "10-Q",
                        "fy": 2024,
                        "fp": "Q1",
                        "val": 100,
                        "filed": "2024-04-20",
                        "accn": "1",
                    }
                ]
            },
        }
    )

    assert extract_metric(facts, "Earnings Per Share", 2024, "Q1") == 2.0


def test_cmc_fiscal_q4_is_not_misclassified_as_fiscal_q3() -> None:
    facts = _company_facts(
        {
            "SalesRevenueGoodsNet": {
                "USD": [
                    {
                        "start": "2021-06-01",
                        "end": "2021-08-31",
                        "form": "10-Q",
                        "fy": 2021,
                        "fp": "Q4",
                        "val": 2030646000.0,
                        "filed": "2021-09-30",
                        "accn": "1",
                    }
                ]
            }
        }
    )

    facts["cik"] = 22444
    assert extract_metric(facts, "Net Sales", 2021, "Q3") is None


def test_annual_ytd_label_does_not_shift_fiscal_year_or_derived_q4_end() -> None:
    facts = _company_facts(
        {
            "NetIncomeLoss": {
                "USD": [
                    {"start": "2020-09-01", "end": "2021-05-31", "fy": 2021, "fp": "FY", "val": 260},
                    {"start": "2020-09-01", "end": "2021-08-31", "fy": 2021, "fp": "FY", "val": 412},
                ]
            },
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [
                    {"end": "2021-05-31", "fy": 2021, "fp": "FY", "val": 501},
                ]
            },
        }
    )

    assert extract_period_end(facts, 2021, "FY") == "2021-08-31"
    assert extract_period_end(facts, 2021, "Q4") == "2021-08-31"


def test_interest_expense_uses_annual_operating_tag_when_quarters_use_standard_tag() -> None:
    facts = _company_facts(
        {
            "InterestExpense": {
                "USD": [
                    {"start": "2024-09-01", "end": "2024-11-30", "fy": 2025, "fp": "Q1", "val": 11_322_000},
                    {"start": "2024-12-01", "end": "2025-02-28", "fy": 2025, "fp": "Q2", "val": 11_167_000},
                    {"start": "2025-03-01", "end": "2025-05-31", "fy": 2025, "fp": "Q3", "val": 10_864_000},
                ]
            },
            "InterestExpenseOperating": {
                "USD": [
                    {"start": "2024-09-01", "end": "2025-08-31", "fy": 2025, "fp": "FY", "val": 45_498_000},
                ]
            },
        }
    )

    facts["cik"] = 22444
    assert extract_metric(facts, "Interest Expense", 2025, "FY") == 45_498_000
    assert extract_metric(facts, "Interest Expense", 2025, "Q4") == 12_145_000
