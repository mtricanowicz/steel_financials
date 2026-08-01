"""Filtered Comparisons page.

Lets the user compare selected metrics across steelmakers and periods,
optionally against a base steelmaker, with table, time-series, and
percent-difference charts per metric.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.data import load_financials, split_by_period
from lib.formatting import (
    STEELMAKER_COLORS,
    STEELMAKER_GROUPS,
    STEELMAKER_NAMES,
    CENTS_METRICS,
    CURRENCY_METRICS,
    DISPLAY_EXCLUDED_METRICS,
    EPS_DOLLAR_METRICS,
    METRIC_DEFINITIONS,
    METRIC_GROUPS,
    MILLIONS_METRICS,
    PERCENT_METRICS,
    color_positive_negative,
    display_metric_name,
    format_metric_value,
    pct_diff,
    scale_metric_for_display,
    steelmaker_label_html,
)

st.header(":material/finance_mode: Filtered Comparisons")


@st.dialog("Metric Definitions", width="large")
def show_metric_definitions() -> None:
    for metric, definition in METRIC_DEFINITIONS:
        st.markdown(f"**{metric}** - {definition}")


financials = load_financials()
if financials.empty:
    st.warning("No financial data found. Run the data build first (see core/README.md).")
    st.stop()

fy_data, q_data = split_by_period(financials)


def _uses_aligned_quarters(data_type: str, data: pd.DataFrame) -> bool:
    return data_type == "Quarterly" and "AlignedPeriod" in data.columns


def _period_columns(data_type: str, data: pd.DataFrame) -> tuple[str, str, str]:
    if _uses_aligned_quarters(data_type, data):
        return "AlignedYear", "AlignedQuarter", "AlignedPeriod"
    return "Year", "Quarter", "Period"


def _dedupe_aligned_rows(df: pd.DataFrame, period_col: str) -> pd.DataFrame:
    """Keep one row per steelmaker/aligned period, preferring the latest reported context."""
    if period_col == "Period" or df.empty:
        return df
    sort_cols = [column for column in ["Steelmaker", period_col, "Reported End", "Period", "Quarter"] if column in df.columns]
    deduped = df.sort_values(sort_cols).drop_duplicates(subset=["Steelmaker", period_col], keep="last")
    return deduped


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
with st.expander("Set filters", expanded=True):
    with st.container(border=True):
        col1, col2, col3 = st.columns([1, 3, 1])
        with col1:
            data_type = st.radio("View Full Year or Quarterly data?", ["Full Year", "Quarterly"], horizontal=True)
        data = fy_data if data_type == "Full Year" else q_data
        period_year_col, period_quarter_col, period_col = _period_columns(data_type, data)

        years = sorted(data[period_year_col].dropna().unique())
        with col2:
            selected_years = st.multiselect("Select Years for comparison:", years, default=years)
        selected_years = selected_years or years

        with col3:
            if data_type == "Quarterly":
                quarters = sorted(data[period_quarter_col].dropna().unique())
                selected_quarters = st.multiselect("Select Quarters for comparison:", quarters, default=quarters)
                selected_quarters = selected_quarters or quarters
            else:
                selected_quarters = ["FY"]

    steelmakers = sorted(data["Steelmaker"].unique())
    with st.container(border=True):
        col4, col5, col6 = st.columns([1, 3, 1])
        with col4:
            steelmaker_group = st.radio(
                "Select Steelmakers for comparison:",
                [
                    "All",
                    *[group for group in STEELMAKER_GROUPS if group != "Defunct Steelmakers"],
                ],
                horizontal=False,
                index=0,
            )
        if steelmaker_group == "All":
            steelmaker_options = steelmakers
            default_steelmakers = [s for s in steelmaker_options if s not in STEELMAKER_GROUPS["Defunct Steelmakers"]]
        elif steelmaker_group in STEELMAKER_GROUPS:
            steelmaker_options = [
                s
                for s in STEELMAKER_GROUPS[steelmaker_group]
            ]
            default_steelmakers = [s for s in steelmaker_options if s not in STEELMAKER_GROUPS["Defunct Steelmakers"]]
        else:
            steelmaker_options = steelmakers
            default_steelmakers = steelmaker_options
        with col5:
            selected_steelmakers = st.multiselect("Add or remove Steelmakers to compare:", steelmaker_options, default=default_steelmakers)
            selected_steelmakers = selected_steelmakers or ["NUE"]
            st.markdown(
                " | ".join(
                    [
                        steelmaker_label_html(
                            steelmaker,
                            text=f"{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ({steelmaker})",
                            logo_height_em=0.95,
                            logo_before_text=True,
                            gap_rem=0.25,
                        )
                        for steelmaker in selected_steelmakers
                    ]
                ),
                unsafe_allow_html=True,
            )
        with col6:
            compare = (
                st.toggle(
                    "Would you like to compare selected steelmakers' metrics against one of the steelmakers?",
                    value=len(selected_steelmakers) > 1,
                )
                if len(selected_steelmakers) > 1
                else False
            )
            base_steelmaker = (
                st.selectbox("Select Steelmaker to compare against:", selected_steelmakers)
                if compare
                else selected_steelmakers[0]
            )

    available_metrics = [
        column
        for column in data.columns
        if column not in ("Year", "Quarter", "Steelmaker", "Period", "Reported End", "AlignedYear", "AlignedQuarter", "AlignedPeriod") and column not in DISPLAY_EXCLUDED_METRICS
    ]
    default_metric_group_index = 0
    with st.container(border=True):
        col7, col8, col9 = st.columns([1, 3, 1])
        with col7:
            metric_group = st.radio(
                "Select Metrics for Comparison:",
                ["All", *METRIC_GROUPS.keys()],
                horizontal=False,
                index=default_metric_group_index,
            )
        if metric_group == "All":
            metric_options = available_metrics
            default_metrics = metric_options
        else:
            metric_options = [metric for metric in METRIC_GROUPS[metric_group] if metric in available_metrics]
            default_metrics = metric_options
        with col8:
            selected_metrics = st.multiselect(
                "Add or remove Metrics to compare:",
                metric_options,
                default=default_metrics,
                format_func=display_metric_name,
            )
        with col9:
            if st.button("Show definitions of the available metrics", icon=":material/dictionary:"):
                show_metric_definitions()


# ---------------------------------------------------------------------------
# Filter and compute
# ---------------------------------------------------------------------------
show_time = False
show_compare = False

mask = (
    data["Steelmaker"].isin(selected_steelmakers)
    & data[period_year_col].isin(selected_years)
    & data[period_quarter_col].isin(selected_quarters)
)
filtered = data[mask].copy().sort_values(period_col)
filtered = _dedupe_aligned_rows(filtered, period_col)
if filtered.empty:
    st.info("No rows match the selected filters.")
    st.stop()

if _uses_aligned_quarters(data_type, filtered):
    st.caption(
        "Quarterly peer comparisons are aligned by calendar timeframe. Table cells and chart hovers still show each company's true reported fiscal quarter."
    )

visible_metrics: list[str] = [
    metric
    for metric in selected_metrics
    if metric in filtered.columns and filtered[metric].notna().any()
]
hidden_metrics: list[str] = [metric for metric in selected_metrics if metric not in visible_metrics]

# Guard against duplicate display labels after metric aliasing.
_seen_labels: set[str] = set()
deduped_visible_metrics: list[str] = []
for metric in visible_metrics:
    label = display_metric_name(metric)
    if label in _seen_labels:
        continue
    _seen_labels.add(label)
    deduped_visible_metrics.append(metric)
visible_metrics = deduped_visible_metrics

if hidden_metrics:
    st.caption(
        "The following metrics are hidden because there is no data available for the selected steelmakers/periods: "
        + ", ".join(display_metric_name(metric) for metric in hidden_metrics)
    )

if not visible_metrics:
    fallback_metrics = [
        metric
        for metric in available_metrics
        if metric in filtered.columns and filtered[metric].notna().any()
    ]
    preferred_fallback_order = ["Net Sales", "Net Income Attributable to Stockholders"]
    preferred_fallback_metrics = [metric for metric in preferred_fallback_order if metric in fallback_metrics]
    if not selected_metrics:
        if preferred_fallback_metrics:
            visible_metrics = preferred_fallback_metrics
            if len(preferred_fallback_metrics) == 2:
                st.info(
                    "You have no metrics selected. Showing Net Sales and Net Earnings. "
                    "Please make a selection."
                )
            else:
                st.info(
                    "You have no metrics selected. "
                    f"Showing {display_metric_name(preferred_fallback_metrics[0])}. Please make a selection."
                )
        elif fallback_metrics:
            visible_metrics = [fallback_metrics[0]]
            st.info(
                "You have no metrics selected. "
                f"Showing {display_metric_name(fallback_metrics[0])}. Please make a selection."
            )
    elif fallback_metrics:
        if preferred_fallback_metrics:
            visible_metrics = preferred_fallback_metrics
            st.info(
                "None of the selected metrics currently have values for this filter set. "
                f"Showing {' and '.join(display_metric_name(metric) for metric in preferred_fallback_metrics)} instead. "
                "Please adjust your filter selection."
            )
        else:
            visible_metrics = [fallback_metrics[0]]
            st.info(
                "None of the selected metrics currently have values for this filter set. "
                f"Showing {display_metric_name(fallback_metrics[0])} instead. Please adjust your filter selection."
            )
    else:
        st.info("No metrics have available values for the current filters.")
        st.stop()

scaled_metrics: dict[str, tuple[pd.DataFrame, str]] = {}
_SMALL_DATA_FASTPATH_ROWS = 5000
for metric in visible_metrics:
    if len(filtered) <= _SMALL_DATA_FASTPATH_ROWS:
        metric_df = filtered.copy()
    else:
        base_cols = [period_col, "Period", "Quarter", "Steelmaker", metric]
        metric_df = filtered[[column for column in base_cols if column in filtered.columns]].copy()
    scaled_metrics[metric] = scale_metric_for_display(metric_df, metric)

periods = sorted(filtered[period_col].unique())
steelmaker_order = sorted(selected_steelmakers)
show_time = len(selected_years) > 1 or len(selected_quarters) > 1
show_compare = len(selected_steelmakers) > 1 and compare

tab_time, tab_period = st.tabs(["Metrics Over Time", "Single Period"])

with tab_time:
    for metric in visible_metrics:
        metric_label = display_metric_name(metric)
        st.subheader(metric_label, divider="gray")
        plot_df, display_col = scaled_metrics[metric]

        if show_time and show_compare:
            col_table, col_line, col_bar = st.columns(3)
        elif show_time:
            col_table, col_line = st.columns([2, 3])
            col_bar = None
        elif show_compare:
            col_table, col_bar = st.columns(2)
            col_line = None
        else:
            (col_table,) = st.columns(1)
            col_line = col_bar = None

        rows = []
        base_series = (
            plot_df[plot_df["Steelmaker"] == base_steelmaker]
            .set_index(period_col)[display_col]
            .reindex(periods)
        )
        for steelmaker in selected_steelmakers:
            steelmaker_rows = plot_df[plot_df["Steelmaker"] == steelmaker].set_index(period_col)
            series = steelmaker_rows[display_col].reindex(periods)
            diffs = [pct_diff(base, comp) for base, comp in zip(base_series, series)]
            rows.append(
                pd.DataFrame(
                    {
                        period_col: periods,
                        "Steelmaker": steelmaker,
                        display_col: [format_metric_value(value, metric) for value in series],
                        f"vs {base_steelmaker}": [
                            None if diff is None or pd.isna(diff) else f"{diff}%" for diff in diffs
                        ],
                    }
                )
            )
        table = pd.concat(rows).set_index([period_col, "Steelmaker"])
        if not show_compare:
            table = table.drop(columns=[f"vs {base_steelmaker}"])
        table = table.unstack("Steelmaker")
        table.columns = table.columns.swaplevel(0, 1)
        table = table.sort_index(axis=1, level=0)
        with col_table:
            if show_compare:
                table = table.drop(columns=[(base_steelmaker, f"vs {base_steelmaker}")], errors="ignore")
                color_cols = [
                    (steelmaker, f"vs {base_steelmaker}")
                    for steelmaker in table.columns.get_level_values(0).unique()
                    if (steelmaker, f"vs {base_steelmaker}") in table.columns
                ]
                styled = table.style.map(color_positive_negative, subset=color_cols)
                st.dataframe(styled, width="stretch")
            else:
                st.dataframe(table, width="stretch")

        if show_time:
            with col_line:
                fig = px.line(
                    plot_df,
                    x=period_col,
                    y=display_col,
                    color="Steelmaker",
                    category_orders={period_col: periods, "Steelmaker": steelmaker_order},
                    color_discrete_map=STEELMAKER_COLORS,
                    title=f"{metric_label} Over Time",
                    custom_data=[column for column in ["Quarter", "Period"] if column in plot_df.columns],
                )
                fig.update_layout(xaxis_title=None, xaxis_tickangle=-45)
                fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.25)
                if metric in CURRENCY_METRICS:
                    hover = "%{x}<br>%{y:$,.0f}"
                elif metric in EPS_DOLLAR_METRICS:
                    hover = "%{x}<br>%{y:$,.2f}"
                elif metric in CENTS_METRICS:
                    hover = "%{x}<br>%{y:.2f}\u00A2"
                elif metric in PERCENT_METRICS:
                    hover = "%{x}<br>%{y:.2f}%"
                else:
                    hover = "%{x}<br>%{y:,.0f}"
                if _uses_aligned_quarters(data_type, plot_df):
                    offset_traces = set(
                        plot_df.loc[
                            plot_df["Period"].ne(plot_df[period_col]),
                            "Steelmaker",
                        ]
                    )
                    fig.update_traces(hovertemplate=hover)
                    fig.for_each_trace(
                        lambda trace: trace.update(
                            hovertemplate=f"{hover}<br>Fiscal Period: %{{customdata[1]}}"
                        )
                        if trace.name in offset_traces
                        else None
                    )
                else:
                    fig.update_traces(hovertemplate=hover)
                st.plotly_chart(fig, width="stretch")

        if show_compare:
            with col_bar:
                diff_rows = []
                for steelmaker in selected_steelmakers:
                    if steelmaker == base_steelmaker:
                        continue
                    series = (
                        plot_df[plot_df["Steelmaker"] == steelmaker]
                        .set_index(period_col)[display_col]
                        .reindex(periods)
                    )
                    diffs = [pct_diff(base, comp) for base, comp in zip(base_series, series)]
                    diff_rows.append(
                        pd.DataFrame({period_col: periods, "Steelmaker": steelmaker, "Percent Difference": diffs})
                    )
                diff_df = pd.concat(diff_rows)
                fig_bar = px.bar(
                    diff_df,
                    x=period_col,
                    y="Percent Difference",
                    color="Steelmaker",
                    barmode="group",
                    category_orders={period_col: periods, "Steelmaker": steelmaker_order},
                    color_discrete_map=STEELMAKER_COLORS,
                    title=f"Percent Difference in {metric_label} vs {base_steelmaker}",
                )
                fig_bar.update_layout(xaxis_title=None, xaxis_tickangle=-45)
                fig_bar.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.75)
                fig_bar.update_traces(hovertemplate="%{x}<br>%{y:.2f}%")
                st.plotly_chart(fig_bar, width="stretch")

with tab_period:
    latest = max(periods)
    st.subheader(f"Summary of {latest}", divider="gray")
    st.caption("When multiple periods are selected, this shows the latest one in the range.")
    grouped_order = [metric for group in METRIC_GROUPS.values() for metric in group]
    preferred_period_metrics = [metric for metric in grouped_order if metric in visible_metrics]
    remaining_period_metrics = [metric for metric in visible_metrics if metric not in preferred_period_metrics]
    period_metrics = preferred_period_metrics + remaining_period_metrics
    metric_order: list[str] = []
    summary_rows = []
    for metric in period_metrics:
        scaled, display_col = scaled_metrics[metric]
        scaled = scaled[scaled[period_col] == latest]
        metric_order.append(display_col)
        base_val = scaled[scaled["Steelmaker"] == base_steelmaker][display_col]
        base_val = base_val.iloc[0] if not base_val.empty else None
        for steelmaker in selected_steelmakers:
            steelmaker_rows = scaled[scaled["Steelmaker"] == steelmaker]
            cell = steelmaker_rows[display_col]
            value = cell.iloc[0] if not cell.empty else None
            diff = pct_diff(base_val, value)
            summary_rows.append(
                {
                    "Metric": display_col,
                    "Steelmaker": steelmaker,
                    latest: format_metric_value(value, metric),
                    f"vs {base_steelmaker}": None if diff is None or pd.isna(diff) else f"{diff}%",
                }
            )
    summary = pd.DataFrame(summary_rows).set_index(["Metric", "Steelmaker"])
    if not show_compare:
        summary = summary.drop(columns=[f"vs {base_steelmaker}"])
    summary = summary.unstack("Steelmaker")
    summary.columns = summary.columns.swaplevel(0, 1)
    summary = summary.sort_index(axis=1, level=0)
    metric_order = list(dict.fromkeys(metric_order))
    summary = summary.reindex(metric_order)
    if show_compare:
        summary = summary.drop(columns=[(base_steelmaker, f"vs {base_steelmaker}")], errors="ignore")
        color_cols = [
            (steelmaker, f"vs {base_steelmaker}")
            for steelmaker in summary.columns.get_level_values(0).unique()
            if (steelmaker, f"vs {base_steelmaker}") in summary.columns
        ]
        st.dataframe(summary.style.map(color_positive_negative, subset=color_cols), width="stretch")
    else:
        st.dataframe(summary, width="stretch")
