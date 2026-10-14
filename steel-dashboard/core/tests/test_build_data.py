import pandas as pd
import pytest

from scripts import build_data


def _existing_financials():
    return pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Period": "2024Q1",
                "Net Sales": 100.0,
                "Cost of Goods Sold": 60.0,
                "Long-Term Debt": 40.0,
                "Current Maturities": pd.NA,
                "EBITDA": 500.0,
            },
            {
                "Steelmaker": "STLD",
                "Year": 2024,
                "Quarter": "Q1",
                "Period": "2024Q1",
                "Net Sales": 200.0,
                "Cost of Goods Sold": 120.0,
                "EBITDA": 700.0,
            },
        ]
    )


def test_manual_only_merge_handles_empty_auto_and_manual_fallback():
    auto = pd.DataFrame()
    manual = pd.DataFrame(
        [{"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Net Sales": 125.0}]
    )

    merged = build_data.merge_sources(auto, manual)

    assert merged.loc[0, "Net Sales"] == 125.0


def test_manual_fallback_handles_auto_rows_without_overlapping_metric_columns():
    auto = pd.DataFrame(
        [
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1"},
            {"Steelmaker": "STLD", "Year": 2024, "Quarter": "Q1"},
        ]
    )
    manual = pd.DataFrame(
        [
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Long-Term Debt": 0.0},
            {"Steelmaker": "STLD", "Year": 2024, "Quarter": "Q1", "Long-Term Debt": 25.0},
        ]
    )

    merged = build_data.merge_sources(auto, manual)

    assert merged["Long-Term Debt"].tolist() == [0.0, 25.0]


def test_combined_sources_prefer_non_null_auto_values_and_log_mismatch(caplog):
    auto = pd.DataFrame(
        [
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Net Sales": 100.0},
            {"Steelmaker": "STLD", "Year": 2024, "Quarter": "Q1", "Net Sales": pd.NA},
        ]
    )
    manual = pd.DataFrame(
        [
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Net Sales": 80.0},
            {"Steelmaker": "STLD", "Year": 2024, "Quarter": "Q1", "Net Sales": 0.0},
        ]
    )

    merged = build_data.merge_sources(auto, manual)

    assert merged["Net Sales"].tolist() == [100.0, 0.0]
    assert "auto=100 manual=80" in caplog.text


def test_incremental_merge_fills_only_missing_values_and_preserves_other_rows():
    existing = _existing_financials()
    new = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Net Sales": 999.0,
                "Current Maturities": 0.0,
            },
            {
                "Steelmaker": "CLF",
                "Year": 2024,
                "Quarter": "Q1",
                "Net Sales": 80.0,
                "Cost of Goods Sold": 50.0,
            },
        ]
    )

    merged = build_data._merge_financials(existing, new, sources=["manual"])

    nue = merged.set_index(["Steelmaker", "Year", "Quarter"]).loc[("NUE", 2024, "Q1")]
    assert nue["Net Sales"] == 100.0
    assert nue["Current Maturities"] == 0.0
    assert nue["Total Debt"] == 40.0
    assert nue["EBITDA"] == 500.0
    assert set(merged["Steelmaker"]) == {"NUE", "STLD", "CLF"}


def test_overwrite_replaces_only_populated_selected_metrics():
    existing = _existing_financials()
    new = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Net Sales": 0.0,
                "Cost of Goods Sold": pd.NA,
            }
        ]
    )

    merged = build_data._merge_financials(
        existing,
        new,
        overwrite=True,
        sources=["manual"],
    )

    nue = merged.set_index(["Steelmaker", "Year", "Quarter"]).loc[("NUE", 2024, "Q1")]
    assert nue["Net Sales"] == 0.0
    assert nue["Cost of Goods Sold"] == 60.0
    assert nue["EBITDA"] == 500.0
    assert "STLD" in set(merged["Steelmaker"])


def test_incomplete_manual_ebitda_update_preserves_stored_value(caplog):
    existing = _existing_financials().iloc[[0]]
    manual = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Net Income": 100.0,
                "Net Sales": 110.0,
            }
        ]
    )

    merged = build_data._merge_financials(
        existing,
        manual,
        overwrite=True,
        sources=["manual"],
    )

    assert merged.loc[0, "EBITDA"] == 500.0
    assert "Preserved stored EBITDA" in caplog.text


def test_complete_manual_ebitda_inputs_recalculate_stored_value():
    existing = _existing_financials().iloc[[0]]
    manual = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Net Income": 100.0,
                "Interest Expense": 1.0,
                "Tax Expense": 2.0,
                "Losses & Impairment of Assets": 3.0,
                "Depreciation & Amortization": 10.0,
            }
        ]
    )

    merged = build_data._merge_financials(
        existing,
        manual,
        overwrite=True,
        sources=["manual"],
    )

    assert merged.loc[0, "EBITDA"] == 116.0


def test_partial_derived_inputs_are_numeric_and_keep_prior_alignment():
    existing = pd.DataFrame(
        [
            {
                "Steelmaker": "CMC",
                "Year": 2026,
                "Quarter": "Q1",
                "Period": "2026Q1",
                "AlignedYear": 2025,
                "AlignedQuarter": "Q4",
                "AlignedPeriod": "2025Q4",
                "Gross Income": 25.0,
                "Gross Margin": 25.0,
            }
        ]
    )
    manual = pd.DataFrame(
        [{"Steelmaker": "CMC", "Year": 2026, "Quarter": "Q1", "Long-Term Debt": 10.0}]
    )

    merged = build_data._merge_financials(existing, manual, sources=["manual"])

    assert merged.loc[0, "AlignedYear"] == 2025
    assert merged.loc[0, "AlignedQuarter"] == "Q4"
    assert merged.loc[0, "Gross Income"] == 25.0
    assert merged.loc[0, "Gross Margin"] == 25.0


def test_new_cmc_manual_period_uses_reporting_quarter_alignment_without_reported_end():
    result = build_data.add_derived(
        pd.DataFrame(
            [
                {
                    "Steelmaker": "CMC",
                    "Year": 2026,
                    "Quarter": "Q1",
                    "Long-Term Debt": 10.0,
                }
            ]
        )
    )

    assert result.loc[0, "AlignedYear"] == 2025
    assert result.loc[0, "AlignedQuarter"] == "Q4"
    assert result.loc[0, "AlignedPeriod"] == "2025Q4"


def test_public_schema_round_trip_keeps_reporting_period_as_update_key():
    internal = pd.DataFrame(
        [
            {
                "Steelmaker": "CMC",
                "Year": 2026,
                "Quarter": "Q1",
                "Period": "2026Q1",
                "Reported End": "2025-11-30",
                "AlignedYear": 2025,
                "AlignedQuarter": "Q4",
                "AlignedPeriod": "2025Q4",
            }
        ]
    )

    round_trip = build_data._from_public_schema(build_data._to_public_schema(internal))

    assert round_trip.loc[0, ["Year", "Quarter", "Period"]].tolist() == [
        2026,
        "Q1",
        "2026Q1",
    ]
    assert round_trip.loc[0, ["AlignedYear", "AlignedQuarter", "AlignedPeriod"]].tolist() == [
        2025,
        "Q4",
        "2025Q4",
    ]


def test_repeated_manual_update_is_idempotent():
    existing = _existing_financials().iloc[[0]]
    new = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2024,
                "Quarter": "Q1",
                "Current Maturities": 0.0,
            }
        ]
    )

    once = build_data._merge_financials(existing, new, sources=["manual"])
    twice = build_data._merge_financials(once, new, sources=["manual"])

    assert len(twice) == 1
    assert twice.loc[0, "Current Maturities"] == 0.0
    assert twice.loc[0, "Total Debt"] == 40.0


def test_manual_build_skips_auto_and_share_history_is_unscoped(monkeypatch):
    manual = pd.DataFrame(
        [
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Long-Term Debt": 0.0},
            {"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q2", "Long-Term Debt": 10.0},
        ]
    )
    repurchases = pd.DataFrame(
        [
            {
                "Steelmaker": "NUE",
                "Year": 2020,
                "Quarter": "FY",
                "Shares Repurchased": 100.0,
                "Cost": 1000.0,
            }
        ]
    )
    written = {}
    diagnostic_args = {}

    def unexpected_auto(*args, **kwargs):
        pytest.fail("manual-only builds must not request SEC data")

    monkeypatch.setattr(build_data, "load_auto", unexpected_auto)
    monkeypatch.setattr(
        build_data,
        "load_manual",
        lambda: (manual.copy(), repurchases.copy(), pd.DataFrame()),
    )
    monkeypatch.setattr(build_data, "_load_existing_financials", pd.DataFrame)
    monkeypatch.setattr(
        build_data,
        "_write",
        lambda path, payload: written.update({path.name: payload}),
    )
    monkeypatch.setattr(
        build_data,
        "_write_coverage_diagnostics",
        lambda **kwargs: diagnostic_args.update(kwargs),
    )

    build_data.build(
        ["NUE"],
        [2024],
        ["Q1"],
        sources=["manual"],
        share_data=True,
    )

    financials = written["financials.json"]
    buybacks = written["buybacks.json"]
    assert len(financials) == 1
    assert financials[0]["Reporting Quarter"] == "Q1"
    assert financials[0]["Long-Term Debt"] == 0.0
    assert buybacks["repurchases"][0]["Period"] == "2020FY"
    assert diagnostic_args["sources"] == ["manual"]
    assert len(diagnostic_args["manual"]) == 1


def test_cli_defaults_to_auto(monkeypatch):
    called = {}
    monkeypatch.setattr(build_data, "build", lambda *args, **kwargs: called.update(kwargs))
    monkeypatch.setattr("sys.argv", ["build_data", "--years", "2024"])

    build_data.main()

    assert called["sources"] == ["auto"]


def test_cli_accepts_combined_sources(monkeypatch):
    called = {}
    monkeypatch.setattr(build_data, "build", lambda *args, **kwargs: called.update(kwargs))
    monkeypatch.setattr(
        "sys.argv",
        ["build_data", "--years", "2024", "--sources", "auto", "manual"],
    )

    build_data.main()

    assert called["sources"] == ["auto", "manual"]


def test_diagnostics_report_skipped_and_failed_sources():
    final_slice = pd.DataFrame(
        [{"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Net Sales": pd.NA}]
    )
    auto = pd.DataFrame(columns=["Steelmaker", "Year", "Quarter"])
    manual = pd.DataFrame()
    failure = [{"steelmaker": "NUE", "error": "company facts unavailable"}]

    _, detail, report = build_data._build_coverage_diagnostics(
        final_slice,
        auto,
        manual,
        ["NUE"],
        [2024],
        ["Q1"],
        ["auto"],
        failure,
    )

    assert report["requested_sources"] == ["auto"]
    assert report["skipped_sources"] == ["manual"]
    assert report["source_status"]["auto"]["status"] == "failed"
    assert report["source_status"]["manual"]["status"] == "skipped"
    assert report["extraction_failures"] == failure
    assert "AUTO_EXTRACTION_FAILED" in set(detail["reason_code"])


def test_manual_source_status_is_reported_as_loaded():
    final_slice = pd.DataFrame(
        [{"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q1", "Net Sales": 1.0}]
    )
    manual = final_slice[["Steelmaker", "Year", "Quarter", "Net Sales"]]

    _, _, report = build_data._build_coverage_diagnostics(
        final_slice,
        pd.DataFrame(),
        manual,
        ["NUE"],
        [2024],
        ["Q1"],
        ["manual"],
        [],
    )

    assert report["source_status"]["auto"]["status"] == "skipped"
    assert report["source_status"]["manual"]["status"] == "loaded"


def test_empty_partial_inputs_do_not_raise():
    result = build_data.add_derived(
        pd.DataFrame(
            [{"Steelmaker": "NUE", "Year": 2024, "Quarter": "Q2", "Net Sales": 100.0}]
        )
    )

    assert len(result) == 1
    assert pd.isna(result.loc[0, "Gross Income"])
