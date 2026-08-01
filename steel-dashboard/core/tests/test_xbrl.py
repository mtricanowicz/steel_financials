"""Unit tests for XBRL metric extraction behaviors."""

from __future__ import annotations

from sec_pipeline.xbrl import extract_metric


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
