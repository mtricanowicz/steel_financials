"""Unit tests for the deterministic, offline-friendly parts of the pipeline."""

from datetime import datetime

import pandas as pd
import pytest

from sec_pipeline.chunk import chunk_text
from sec_pipeline.config import PeriodSpec, build_periods
from sec_pipeline.edgar_client import _RateLimiter
from sec_pipeline.parse import clean_text, html_to_text
from scripts.build_data import add_derived
from sec_pipeline.xbrl import extract_financials, extract_metric


class TestChunk:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("A short paragraph.", chunk_size=1200)
        assert chunks == ["A short paragraph."]

    def test_long_text_splits_with_overlap(self):
        text = " ".join(f"Sentence number {i}." for i in range(400))
        chunks = chunk_text(text, chunk_size=200, overlap=40)
        assert len(chunks) > 1
        assert all(len(c) <= 260 for c in chunks)  # size + tolerance

    def test_empty_text(self):
        assert chunk_text("") == []


class TestParse:
    def test_clean_text_collapses_inline_whitespace(self):
        assert clean_text("a\t  b   c") == "a b c"

    def test_clean_text_preserves_paragraphs(self):
        assert clean_text("para one\n\n\n\npara two") == "para one\n\npara two"

    def test_html_to_text_drops_markup(self):
        html = "<html><body><p>Revenue rose.</p><script>x=1</script></body></html>"
        out = html_to_text(html.encode())
        assert "Revenue rose." in out
        assert "x=1" not in out


class TestPeriodSpec:
    def test_label_roundtrip(self):
        spec = PeriodSpec(2024, "Q2")
        assert spec.label == "2024Q2"
        assert PeriodSpec.from_label("2024Q2") == spec

    def test_invalid_period_rejected(self):
        with pytest.raises(ValueError):
            PeriodSpec(2024, "Q5")

    def test_quarter_window(self):
        start, end = PeriodSpec(2024, "Q2").date_window()
        assert start == datetime(2024, 4, 1)
        assert end.month in (7, 8)  # padded past quarter close

    def test_fy_window_spans_year(self):
        start, end = PeriodSpec(2024, "FY").date_window()
        assert start == datetime(2024, 1, 1)
        assert end.year == 2025  # padded into the next year

    def test_build_periods_cartesian(self):
        specs = build_periods([2023, 2024], ["Q1", "FY"])
        assert len(specs) == 4
        assert PeriodSpec(2023, "Q1") in specs


class TestRateLimiter:
    def test_enforces_minimum_interval(self):
        import time

        limiter = _RateLimiter(max_per_second=50.0)
        start = time.monotonic()
        for _ in range(5):
            limiter.wait()
        assert time.monotonic() - start >= 4 * (1 / 50.0)


class TestXbrlFiscalFallback:
    def test_fp_fallback_handles_non_calendar_q1(self):
        # Simulate a CMC-like fiscal quarter: FY2026 Q1 ends in prior calendar year.
        facts = {
            "facts": {
                "us-gaap": {
                    "SalesRevenueGoodsNet": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-09-01",
                                    "end": "2025-11-30",
                                    "val": 123.0,
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                    "filed": "2026-01-10",
                                    "accn": "0000000000-26-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Net Sales",
            2026,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 123.0

    def test_fp_fallback_can_be_disabled(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "SalesRevenueGoodsNet": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-09-01",
                                    "end": "2025-11-30",
                                    "val": 123.0,
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                    "filed": "2026-01-10",
                                    "accn": "0000000000-26-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Net Sales",
            2026,
            "Q1",
            enable_fp_fallback=False,
        )
        assert value is None

    def test_extract_financials_includes_reported_end_for_fiscal_offset_quarter(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "SalesRevenueGoodsNet": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-09-01",
                                    "end": "2025-11-30",
                                    "val": 123.0,
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                    "filed": "2026-01-10",
                                    "accn": "0000000000-26-000001",
                                }
                            ]
                        }
                    },
                    "LongTermDebtCurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2025-11-30",
                                    "val": 45.0,
                                    "form": "10-Q",
                                    "fy": 2026,
                                    "fp": "Q1",
                                    "filed": "2026-01-10",
                                    "accn": "0000000000-26-000002",
                                }
                            ]
                        }
                    },
                }
            }
        }

        rows = extract_financials(facts, [2026], ["Q1"], enable_fp_fallback=True)

        assert rows[0]["Reported End"] == "2025-11-30"


class TestBuildDataAlignedPeriods:
    def test_add_derived_aligns_quarter_by_reported_end(self):
        df = add_derived(
            pd.DataFrame(
                [
                    {
                        "Steelmaker": "CMC",
                        "Year": 2026,
                        "Quarter": "Q1",
                        "Reported End": "2025-11-30",
                        "Net Sales": 1000.0,
                        "Cost of Goods Sold": 700.0,
                        "Net Income Attributable to Stockholders": 100.0,
                        "Current Maturities": 10.0,
                        "Long-Term Debt": 90.0,
                        "Cash & Cash Equivalents": 25.0,
                        "Short-Term Investments": 5.0,
                        "Operating Cash Flow": 40.0,
                        "Capital Expenditures": -15.0,
                    }
                ]
            )
        )

        row = df.iloc[0]
        assert row["Period"] == "2026Q1"
        assert row["AlignedYear"] == 2025
        assert row["AlignedQuarter"] == "Q4"
        assert row["AlignedPeriod"] == "2025Q4"

    def test_add_derived_keeps_week_based_calendar_quarter_in_same_quarter(self):
        df = add_derived(
            pd.DataFrame(
                [
                    {
                        "Steelmaker": "NUE",
                        "Year": 2026,
                        "Quarter": "Q1",
                        "Reported End": "2026-04-04",
                        "Net Sales": 1000.0,
                        "Cost of Goods Sold": 700.0,
                        "Net Income Attributable to Stockholders": 100.0,
                        "Current Maturities": 10.0,
                        "Long-Term Debt": 90.0,
                        "Cash & Cash Equivalents": 25.0,
                        "Short-Term Investments": 5.0,
                        "Operating Cash Flow": 40.0,
                        "Capital Expenditures": -15.0,
                    }
                ]
            )
        )

        row = df.iloc[0]
        assert row["AlignedYear"] == 2026
        assert row["AlignedQuarter"] == "Q1"
        assert row["AlignedPeriod"] == "2026Q1"

    def test_add_derived_applies_known_nue_2022_q3_alignment_override(self):
        df = add_derived(
            pd.DataFrame(
                [
                    {
                        "Steelmaker": "NUE",
                        "Year": 2022,
                        "Quarter": "Q3",
                        "Reported End": "2023-07-01",
                        "Net Sales": 1000.0,
                        "Cost of Goods Sold": 700.0,
                        "Net Income Attributable to Stockholders": 100.0,
                        "Current Maturities": 10.0,
                        "Long-Term Debt": 90.0,
                        "Cash & Cash Equivalents": 25.0,
                        "Short-Term Investments": 5.0,
                        "Operating Cash Flow": 40.0,
                        "Capital Expenditures": -15.0,
                    }
                ]
            )
        )

        row = df.iloc[0]
        assert row["AlignedYear"] == 2022
        assert row["AlignedQuarter"] == "Q3"
        assert row["AlignedPeriod"] == "2022Q3"


class TestXbrlSafeCoverageFallbacks:
    def test_long_term_debt_supports_noncurrent_tag(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "LongTermDebtNoncurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2017-04-01",
                                    "val": 3739908000.0,
                                    "form": "10-Q",
                                    "fy": 2017,
                                    "fp": "Q1",
                                    "filed": "2017-05-10",
                                    "accn": "0000000000-17-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Long-Term Debt",
            2017,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 3739908000.0

    def test_current_maturities_supports_debtcurrent_tag(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "DebtCurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2024-03-31",
                                    "val": 275.0,
                                    "form": "10-Q",
                                    "fy": 2024,
                                    "fp": "Q1",
                                    "filed": "2024-05-01",
                                    "accn": "0000000000-24-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Current Maturities",
            2024,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 275.0

    def test_current_maturities_supports_short_term_borrowings_fallback(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "ShortTermBorrowings": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2019-03-30",
                                    "val": 71438000.0,
                                    "form": "10-Q",
                                    "fy": 2019,
                                    "fp": "Q1",
                                    "filed": "2019-05-08",
                                    "accn": "0000000000-19-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Current Maturities",
            2019,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 71438000.0

    def test_attributable_net_income_falls_back_to_net_income(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-03-31",
                                    "val": 510.0,
                                    "form": "10-Q",
                                    "fy": 2024,
                                    "fp": "Q1",
                                    "filed": "2024-05-01",
                                    "accn": "0000000000-24-000002",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Net Income Attributable to Stockholders",
            2024,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 510.0

    def test_attributable_net_income_prefers_direct_tag(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-03-31",
                                    "val": 510.0,
                                    "form": "10-Q",
                                    "fy": 2024,
                                    "fp": "Q1",
                                    "filed": "2024-05-01",
                                    "accn": "0000000000-24-000003",
                                }
                            ]
                        }
                    },
                    "NetIncomeLossAvailableToCommonStockholdersBasic": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-03-31",
                                    "val": 480.0,
                                    "form": "10-Q",
                                    "fy": 2024,
                                    "fp": "Q1",
                                    "filed": "2024-05-01",
                                    "accn": "0000000000-24-000004",
                                }
                            ]
                        }
                    },
                }
            }
        }

        value = extract_metric(
            facts,
            "Net Income Attributable to Stockholders",
            2024,
            "Q1",
            enable_fp_fallback=True,
        )
        assert value == 480.0


class TestXbrlWeekBasedPeriodEnds:
    def test_duration_period_accepts_week_based_quarter_end(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-04-02",
                                    "end": "2023-07-01",
                                    "val": 9523256000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "filed": "2023-08-04",
                                    "accn": "0000000000-23-000001",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Net Sales",
            2023,
            "Q2",
            enable_fp_fallback=True,
        )
        assert value == 9523256000.0

    def test_instant_period_accepts_week_based_quarter_end(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "LongTermDebtAndCapitalLeaseObligationsCurrent": {
                        "units": {
                            "USD": [
                                {
                                    "end": "2023-07-01",
                                    "val": 25087000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "filed": "2023-08-04",
                                    "accn": "0000000000-23-000002",
                                }
                            ]
                        }
                    }
                }
            }
        }

        value = extract_metric(
            facts,
            "Current Maturities",
            2023,
            "Q2",
            enable_fp_fallback=True,
        )
        assert value == 25087000.0


class TestXbrlAdditiveDurationFallbacks:
    def test_q2_q3_q4_can_be_derived_from_ytd_contexts(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2021-01-01",
                                    "end": "2021-03-31",
                                    "val": 10.0,
                                    "form": "10-Q",
                                    "fy": 2021,
                                    "fp": "Q1",
                                    "filed": "2021-05-01",
                                    "accn": "0000000000-21-000001",
                                },
                                {
                                    "start": "2021-01-01",
                                    "end": "2021-06-30",
                                    "val": 25.0,
                                    "form": "10-Q",
                                    "fy": 2021,
                                    "fp": "Q2",
                                    "filed": "2021-08-01",
                                    "accn": "0000000000-21-000002",
                                },
                                {
                                    "start": "2021-01-01",
                                    "end": "2021-09-30",
                                    "val": 45.0,
                                    "form": "10-Q",
                                    "fy": 2021,
                                    "fp": "FY",
                                    "filed": "2021-11-01",
                                    "accn": "0000000000-21-000003",
                                },
                                {
                                    "start": "2021-01-01",
                                    "end": "2021-12-31",
                                    "val": 70.0,
                                    "form": "10-K",
                                    "fy": 2021,
                                    "fp": "FY",
                                    "filed": "2022-02-01",
                                    "accn": "0000000000-22-000004",
                                },
                            ]
                        }
                    }
                }
            }
        }

        q2 = extract_metric(
            facts,
            "Net Income",
            2021,
            "Q2",
            enable_fp_fallback=True,
        )
        q3 = extract_metric(
            facts,
            "Net Income",
            2021,
            "Q3",
            enable_fp_fallback=True,
        )
        q4 = extract_metric(
            facts,
            "Net Income",
            2021,
            "Q4",
            enable_fp_fallback=True,
        )

        assert q2 == 15.0
        assert q3 == 20.0
        assert q4 == 25.0

    def test_comparative_rows_do_not_override_current_fiscal_values(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2021-09-01",
                                    "end": "2021-11-30",
                                    "val": 232889000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q1",
                                    "filed": "2023-01-09",
                                    "accn": "0000000000-23-000014",
                                },
                                {
                                    "start": "2022-09-01",
                                    "end": "2022-11-30",
                                    "val": 261774000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q1",
                                    "filed": "2023-01-09",
                                    "accn": "0000000000-23-000014",
                                },
                                {
                                    "start": "2021-12-01",
                                    "end": "2022-02-28",
                                    "val": 383314000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q2",
                                    "filed": "2023-03-23",
                                    "accn": "0000000000-23-000058",
                                },
                                {
                                    "start": "2022-12-01",
                                    "end": "2023-02-28",
                                    "val": 179849000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q2",
                                    "filed": "2023-03-23",
                                    "accn": "0000000000-23-000058",
                                },
                                {
                                    "start": "2022-03-01",
                                    "end": "2022-05-31",
                                    "val": 312429000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "filed": "2023-06-22",
                                    "accn": "0000000000-23-000077",
                                },
                                {
                                    "start": "2023-03-01",
                                    "end": "2023-05-31",
                                    "val": 233971000.0,
                                    "form": "10-Q",
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "filed": "2023-06-22",
                                    "accn": "0000000000-23-000077",
                                },
                                {
                                    "start": "2020-09-01",
                                    "end": "2021-08-31",
                                    "val": 412865000.0,
                                    "form": "10-K",
                                    "fy": 2023,
                                    "fp": "FY",
                                    "filed": "2023-10-12",
                                    "accn": "0000000000-23-000126",
                                },
                                {
                                    "start": "2022-09-01",
                                    "end": "2023-08-31",
                                    "val": 859760000.0,
                                    "form": "10-K",
                                    "fy": 2023,
                                    "fp": "FY",
                                    "filed": "2023-10-12",
                                    "accn": "0000000000-23-000126",
                                },
                            ]
                        }
                    }
                }
            }
        }

        q1 = extract_metric(facts, "Net Income", 2023, "Q1", enable_fp_fallback=True)
        q2 = extract_metric(facts, "Net Income", 2023, "Q2", enable_fp_fallback=True)
        q3 = extract_metric(facts, "Net Income", 2023, "Q3", enable_fp_fallback=True)
        q4 = extract_metric(facts, "Net Income", 2023, "Q4", enable_fp_fallback=True)
        fy = extract_metric(facts, "Net Income", 2023, "FY", enable_fp_fallback=True)

        assert q1 == 261774000.0
        assert q2 == 179849000.0
        assert q3 == 233971000.0
        assert q4 == 184166000.0
        assert fy == 859760000.0
