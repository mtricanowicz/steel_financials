"""Generate small illustrative datasets for local dashboard development.

This does not touch the network. It combines the manual CSVs with a handful of
placeholder auto-metric values and runs the same merge and derive logic as the
real build, then writes ``financials.json``, ``buybacks.json``, and a minimal
``insights.json`` into ``data/generated/``. Use it to run the app before the
full pipeline has been executed. The auto values are illustrative and must not
be treated as real financials.
"""

from __future__ import annotations

import json

import pandas as pd

from sec_pipeline import config
from scripts.build_data import (
    BUYBACKS_PATH,
    FINANCIALS_PATH,
    add_derived,
    build_buybacks,
    load_manual,
    merge_sources,
)

# Illustrative auto metrics (dollars) keyed by (Steelmaker, Year, Quarter).
_SAMPLE_AUTO = [
    ("NUE", 2024, "FY", 33_740_000_000, 31_200_000_000, 2_200_000_000, 5_000_000_000),
    ("STLD", 2024, "FY", 18_800_000_000, 16_700_000_000, 1_650_000_000, 3_200_000_000),
    ("NUE", 2024, "Q2", 8_900_000_000, 8_100_000_000, 620_000_000, 5_050_000_000),
    ("STLD", 2024, "Q2", 4_700_000_000, 4_200_000_000, 430_000_000, 3_250_000_000),
]


def sample_auto() -> pd.DataFrame:
    cols = ["Steelmaker", "Year", "Quarter", "Net Sales", "Cost of Goods Sold", "Net Income", "Long-Term Debt"]
    return pd.DataFrame(_SAMPLE_AUTO, columns=cols)


def main() -> None:
    manual_metrics, repurchases, sales = load_manual()
    merged = add_derived(merge_sources(sample_auto(), manual_metrics))
    drop = [c for c in merged.columns if c.endswith("_manual")]
    merged = merged.drop(columns=drop).sort_values(["Steelmaker", "Year", "Quarter"])

    FINANCIALS_PATH.write_text(
        json.dumps(json.loads(merged.astype(object).where(pd.notna(merged), None).to_json(orient="records")), indent=2),
        encoding="utf-8",
    )
    BUYBACKS_PATH.write_text(json.dumps(build_buybacks(repurchases, sales), indent=2), encoding="utf-8")

    insights = {
        "NUE": {"2024": {"Q2": "### Financial Insights\n1. Sample insight for NUE 2024 Q2.\n\n### Business Insights\n1. Sample business note.\n\n### Capital Allocation Insights\n1. Sample strategy note."}},
        "STLD": {"2024": {"Q2": "### Financial Insights\n1. Sample insight for STLD 2024 Q2.\n\n### Business Insights\n1. Sample business note.\n\n### Capital Allocation Insights\n1. Sample strategy note."}},
    }
    config.SUMMARIES_PATH.write_text(json.dumps(insights, indent=2), encoding="utf-8")

    print(f"Wrote sample data to {config.GENERATED_DIR}")


if __name__ == "__main__":
    main()
