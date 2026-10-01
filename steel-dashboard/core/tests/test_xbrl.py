"""Unit tests for XBRL metric extraction behaviors."""

from __future__ import annotations

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


def test_cmc_fiscal_q4_august_31_fact_is_accepted_for_calendar_q3_match() -> None:
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

    assert extract_metric(facts, "Net Sales", 2021, "Q3") == 2030646000.0


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

    assert extract_metric(facts, "Interest Expense", 2025, "FY") == 45_498_000
    assert extract_metric(facts, "Interest Expense", 2025, "Q4") == 12_145_000
