"""Insights page.

Displays the precomputed, LLM-generated insights for a selected steel producer, year,
and period. Summaries are produced offline by the core pipeline and read here
from static JSON, so no API calls happen at view time.
"""

from __future__ import annotations

import streamlit as st

from lib.data import load_financials, load_insights
from lib.formatting import STEELMAKER_NAMES, steelmaker_header_html

st.header(":material/emoji_objects: Insights")
st.info(
    "Insights are extracted from steel producer SEC filings and summarized by an "
    "AI model. Summaries may contain inaccuracies.",
    icon=":material/info:",
)

insights = load_insights()
financials = load_financials()

if not insights:
    st.warning("No insights found. Run the insights pipeline first (see core/README.md).")
    st.stop()

steelmakers = sorted([s for s in insights.keys() if s != "ATI" and s != "CRS"])

col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    steelmaker = st.selectbox("Company", steelmakers, index=None, placeholder="Select")
with col2:
    years = sorted(insights.get(steelmaker, {}).keys(), reverse=True) if steelmaker else []
    year = st.selectbox("Year", years, index=None, placeholder="Select")
with col3:
    periods = sorted(insights.get(steelmaker, {}).get(year, {}).keys()) if steelmaker and year else []
    period = st.selectbox("Period", periods, index=None, placeholder="Select")

if not (steelmaker and year and period):
    st.caption("Select a company, year, and period to view insights.")
    st.stop()

name = STEELMAKER_NAMES.get(steelmaker, steelmaker)
st.markdown(
    steelmaker_header_html(
        steelmaker,
        f"{name} ({steelmaker}) | {year}{period}",
        heading_level=3,
        logo_height_em=2.00,
        logo_before_text=True,
        gap_rem=0.55,
    ),
    unsafe_allow_html=True,
)
st.markdown("<div style='border-bottom:1px solid rgba(49, 51, 63, 0.2); margin:0 0 1rem 0;'></div>", unsafe_allow_html=True)

summary = insights.get(steelmaker, {}).get(year, {}).get(period)
if summary:
    st.markdown(summary)
else:
    st.error("No summary is available for the selected period.", icon=":material/report:")
