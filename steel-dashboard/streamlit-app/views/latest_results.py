"""Latest Results page.

Shows the most recent full-year and quarterly figures for all companies and all
metrics, optionally compared against a base company.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from lib.data import load_financials, split_by_period, load_insights
from lib.formatting import (
    DISPLAY_EXCLUDED_METRICS,
    METRIC_GROUPS,
    STEELMAKER_GROUPS,
    STEELMAKER_NAMES,
    METRIC_DEFINITIONS,
    color_positive_negative,
    display_metric_name,
    format_metric_value,
    pct_diff,
    scale_metric_for_display,
    steelmaker_label_html,
    steelmaker_header_html,
)

st.header(":material/calendar_today: Latest Results")


@st.dialog("Metric Definitions", width="large")
def show_metric_definitions() -> None:
    for metric, definition in METRIC_DEFINITIONS:
        st.markdown(f"**{metric}** - {definition}")


insights = load_insights()


financials = load_financials()
if financials.empty:
    st.warning("No financial data found. Run the data build first (see core/README.md).")
    st.stop()

fy_data, q_data = split_by_period(financials)
steelmakers = sorted(financials["Steelmaker"].unique())


def _period_columns(data: pd.DataFrame, *, use_aligned_quarters: bool) -> tuple[str, str, str]:
    if use_aligned_quarters and "AlignedPeriod" in data.columns:
        return "AlignedYear", "AlignedQuarter", "AlignedPeriod"
    return "Year", "Quarter", "Period"


def _dedupe_aligned_rows(df: pd.DataFrame, period_col: str) -> pd.DataFrame:
    """Keep one row per steelmaker/aligned period, preferring the latest reported context."""
    if period_col == "Period" or df.empty:
        return df
    sort_cols = [column for column in ["Steelmaker", period_col, "Reported End", "Period", "Quarter"] if column in df.columns]
    return df.sort_values(sort_cols).drop_duplicates(subset=["Steelmaker", period_col], keep="last")

col_a, col_b = st.columns([4, 1])
with col_b:
    steelmaker_group = st.radio("Select Steelmakers for Comparison:", ["All", *[group for group in STEELMAKER_GROUPS if group != "Defunct Steelmakers"]], horizontal=False, index=0)
    if steelmaker_group == "All":
        steelmakers_options = [s for s in steelmakers if s not in STEELMAKER_GROUPS["Defunct Steelmakers"]]
        default_steelmakers = steelmakers_options
    elif steelmaker_group in STEELMAKER_GROUPS:
        steelmakers_options = [s for s in STEELMAKER_GROUPS[steelmaker_group] if s in steelmakers and s not in STEELMAKER_GROUPS["Defunct Steelmakers"]]
        default_steelmakers = steelmakers_options
    else:
        steelmakers_options = steelmakers
        default_steelmakers = steelmakers_options
    selected_steelmakers = st.multiselect("Add or remove Steelmakers to compare:", steelmakers_options, default=default_steelmakers)
    selected_steelmakers = selected_steelmakers or steelmakers_options[:1]
    st.markdown("<br>".join([steelmaker_label_html(steelmaker, text=f"{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ({steelmaker})", logo_height_em=0.95, logo_before_text=True, gap_rem=0.25) for steelmaker in selected_steelmakers]), unsafe_allow_html=True)
    compare = (
        st.toggle("Compare against a steelmaker?", value=False)
        if len(selected_steelmakers) > 1
        else False
    )
    base_steelmaker = st.selectbox(
        "Select Steelmaker to compare against:",
        selected_steelmakers,
        index = "NUE" in selected_steelmakers and selected_steelmakers.index("NUE") or 0
    ) if compare else selected_steelmakers[0]
    if st.button("Show definitions of the metrics", icon=":material/dictionary:", width="stretch"):
        show_metric_definitions()


def build_summary(data: pd.DataFrame, *, use_aligned_quarters: bool) -> pd.DataFrame:
    """Build a formatted, optionally compared summary of the latest period."""
    _, _, period_col = _period_columns(data, use_aligned_quarters=use_aligned_quarters)
    deduped = _dedupe_aligned_rows(data.copy(), period_col)
    latest = max(deduped[period_col])
    snapshot = deduped[deduped[period_col] == latest].copy()
    available_metrics = [
        c
        for c in snapshot.columns
        if c not in ("Year", "Quarter", "Period", "AlignedYear", "AlignedQuarter", "AlignedPeriod", "Reported End", "Steelmaker") and c not in DISPLAY_EXCLUDED_METRICS
    ]

    # Keep a stable order aligned with the app metric groups.
    grouped_order = [metric for group in METRIC_GROUPS.values() for metric in group]
    grouped_order = [metric for metric in grouped_order if metric in available_metrics]
    remaining = [metric for metric in available_metrics if metric not in grouped_order]
    metrics = grouped_order + remaining

    # Guard against duplicate display labels after metric aliasing.
    seen_labels: set[str] = set()
    deduped_metrics: list[str] = []
    for metric in metrics:
        label = display_metric_name(metric)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        deduped_metrics.append(metric)
    metrics = deduped_metrics

    metric_order: list[str] = []
    rows = []
    value_column = latest
    reported_labels = {
        steelmaker: (
            snapshot.loc[snapshot["Steelmaker"] == steelmaker, "Period"].iloc[0]
            if use_aligned_quarters and not snapshot.loc[snapshot["Steelmaker"] == steelmaker, "Period"].empty
            else (latest if not use_aligned_quarters else "TBA")
        )
        for steelmaker in selected_steelmakers
    }
    for metric in metrics:
        scaled, display_col = scale_metric_for_display(snapshot.copy(), metric)
        metric_order.append(display_col)
        base_cell = scaled[scaled["Steelmaker"] == base_steelmaker][display_col]
        base_val = base_cell.iloc[0] if not base_cell.empty else None
        for steelmaker in selected_steelmakers:
            steelmaker_rows = scaled[scaled["Steelmaker"] == steelmaker]
            cell = steelmaker_rows[display_col]
            value = cell.iloc[0] if not cell.empty else None
            d = pct_diff(base_val, value)
            rows.append(
                {
                    "Metric": display_col,
                    "Steelmaker": steelmaker,
                    value_column: format_metric_value(value, metric) or "TBA",
                    f"vs {base_steelmaker}": None if d is None or pd.isna(d) else f"{d}%",
                }
            )
    summary = pd.DataFrame(rows).set_index(["Metric", "Steelmaker"])
    if not compare:
        summary = summary.drop(columns=[f"vs {base_steelmaker}"])
    summary = summary.unstack("Steelmaker")
    summary.columns = summary.columns.swaplevel(0, 1)
    summary = summary.sort_index(axis=1, level=0)
    if use_aligned_quarters:
        summary.columns = pd.MultiIndex.from_tuples(
            [
                (steelmaker, reported_labels.get(steelmaker, "TBA")) if label == value_column else (steelmaker, label)
                for steelmaker, label in summary.columns
            ]
        )
    metric_order = list(dict.fromkeys(metric_order))
    summary = summary.reindex(metric_order)
    if compare:
        summary = summary.drop(columns=[(base_steelmaker, f"vs {base_steelmaker}")], errors="ignore")
    return summary


def render(data: pd.DataFrame, title: str) -> None:
    if data.empty:
        st.info(f"No data available for {title}.")
        return
    use_aligned_quarters = title == "Most recent quarter"
    _, _, period_col = _period_columns(data, use_aligned_quarters=use_aligned_quarters)
    latest = max(_dedupe_aligned_rows(data.copy(), period_col)[period_col])
    latest_year = latest[:4]
    latest_quarter = latest[4:]
    st.subheader(f"{title}: {latest}", divider="gray")
    if use_aligned_quarters:
        st.caption("Quarterly results are grouped by aligned peer quarter. Each steelmaker header shows its true reported fiscal quarter when available.")
    summary = build_summary(data, use_aligned_quarters=use_aligned_quarters)
    if compare:
        color_cols = [
            (s, f"vs {base_steelmaker}")
            for s in summary.columns.get_level_values(0).unique()
            if (s, f"vs {base_steelmaker}") in summary.columns
        ]
        st.dataframe(summary.style.map(color_positive_negative, subset=color_cols), width="stretch")
    else:
        st.dataframe(summary, width="stretch")
    with st.expander(f"Review {latest} insights", expanded=False, icon=":material/emoji_objects:"):
        insight_steelmaker = st.pills(
            label = None,
            options = selected_steelmakers,
            default = "NUE" if "NUE" in selected_steelmakers else selected_steelmakers[0],
            selection_mode = "single",
            required = True,
            width="stretch",
            key=f"latest_insights_{title}"
        )
        name = STEELMAKER_NAMES.get(insight_steelmaker, insight_steelmaker)
        st.markdown(
            steelmaker_header_html(
                insight_steelmaker,
                f"{name} ({insight_steelmaker}) | {latest_year}{latest_quarter}",
                heading_level=3,
                logo_height_em=2.00,
                logo_before_text=True,
                gap_rem=0.55,
            ),
            unsafe_allow_html=True,
        )
        st.markdown("<div style='border-bottom:1px solid rgba(49, 51, 63, 0.2); margin:0 0 1rem 0;'></div>", unsafe_allow_html=True)
        summary = insights.get(insight_steelmaker, {}).get(latest_year, {}).get(latest_quarter)
        if summary:
            st.markdown(summary)
        else:
            st.error("No summary is available for the selected period.", icon=":material/report:")


with col_a:
    #left, right = st.columns(2)
    #with left:
        render(q_data[q_data["Steelmaker"].isin(selected_steelmakers)], "Most recent quarter")
    #with right:
        render(fy_data[fy_data["Steelmaker"].isin(selected_steelmakers)], "Most recent full year")
