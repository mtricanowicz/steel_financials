"""Steel Financial Dashboard - Streamlit multipage entry point.

Each tab is its own page, so navigating between them does not
re-execute the others. Data loading is cached, and the manual rerun and
session-state workarounds of the original have been removed. Streamlit's natural
rerun-on-interaction model is sufficient once state is derived from widgets and
cached loaders.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from lib.formatting import (
    STEELMAKER_NAMES,
    STEELMAKER_IR,
    STEELMAKER_GROUPS,
    STEELMAKER_DEFUNCT_REASONS,
    steelmaker_label_html,
    get_other_dashboard_link,
    get_about_sidebar_html,
    fixed_stock_ticker_html,
)

from lib.data import (
    fetch_live_quotes,
)

_APP_DIR = Path(__file__).parent
_ASSETS_DIR = _APP_DIR.parent / "assets"
_BRANDING_DIR = _ASSETS_DIR / "branding"
_MARKET_TZ = ZoneInfo("America/New_York")


def _is_market_open(now: dt.datetime | None = None) -> bool:
    """Return True when US equities regular session is open (Mon-Fri, 9:30-16:00 ET)."""
    current = now.astimezone(_MARKET_TZ) if now else dt.datetime.now(_MARKET_TZ)
    if current.weekday() >= 5:
        return False
    session_open = current.replace(hour=9, minute=30, second=0, microsecond=0)
    session_close = current.replace(hour=16, minute=0, second=0, microsecond=0)
    return session_open <= current < session_close


def _next_market_open(now: dt.datetime | None = None) -> dt.datetime:
    """Return the next market-open timestamp in ET."""
    current = now.astimezone(_MARKET_TZ) if now else dt.datetime.now(_MARKET_TZ)
    probe = current
    while True:
        if probe.weekday() < 5:
            open_at = probe.replace(hour=9, minute=30, second=0, microsecond=0)
            if probe < open_at:
                return open_at
        probe = (probe + dt.timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )


def _ticker_run_every() -> str:
    """Use 60s during market hours; otherwise wait until next market open."""
    now = dt.datetime.now(_MARKET_TZ)
    if _is_market_open(now):
        return "60s"

    seconds = int((_next_market_open(now) - now).total_seconds())
    # Keep a small floor to avoid zero/negative values on boundary transitions.
    return f"{max(seconds, 60)}s"

st.set_page_config(
    page_title="Steel Financial Dashboard",
    page_icon=str(_BRANDING_DIR / "site_favicon.png"),
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "About": """
        Explore U.S. commodity steel producer financial performance.

        **Created by:** Michael Tricanowicz
        """
    },
)


# Site logo / title banner, shown on every page.
st.image(
    str(_BRANDING_DIR / "site_title.png"),
    caption="Explore US Steelmaker Financial Performance",
)


# Collapsible sidebar with reference information including sections for About, Steelmakers Covered, and Other Industry Dashboards
# Sidebar also includes a live stock ticker toggle
st.logo(
    str(_BRANDING_DIR / "site_title.png"),
    #link="https://steel.industryfinancials.com",
    icon_image=str(_BRANDING_DIR / "site_favicon.png"),
)
with st.sidebar:
    st.toggle("Activate Stock Ticker", value=True, key="activate_stock_ticker")
    with st.expander("About the Steel Financial Dashboard"):
        st.markdown(
            get_about_sidebar_html(),
            unsafe_allow_html=True
        )
    with st.expander("Steelmakers Covered", expanded=True):
        for group in (g for g in STEELMAKER_GROUPS if g != "Defunct Steelmakers"):
            st.markdown(f"### {group}", unsafe_allow_html=True)
            for steelmaker in STEELMAKER_GROUPS[group]:
                if steelmaker in ["ATI", "CRS"]:
                    continue
                if steelmaker in STEELMAKER_DEFUNCT_REASONS:
                    st.markdown(
                        steelmaker_label_html(
                            steelmaker,
                            text=f"*{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ({steelmaker}) - {STEELMAKER_DEFUNCT_REASONS[steelmaker]}*",
                            logo_height_em=1.25,
                            logo_before_text=True,
                            gap_rem=0.25
                        ),
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        steelmaker_label_html(steelmaker, text=f"{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ([{steelmaker}]({STEELMAKER_IR.get(steelmaker, '#')}))", logo_height_em=1.25, logo_before_text=True, gap_rem=0.25),
                        unsafe_allow_html=True
                    )
        st.markdown("<small><br>Active steelmakers<br>*Defunct steelmakers*</small>", unsafe_allow_html=True)
    with st.expander("Other Industry Dashboards", expanded=True):
        st.markdown(
            get_other_dashboard_link(
                icon_path=_BRANDING_DIR / "site_favicon_airline.png",
                name="Airline Financial Dashboard",
                link="https://airline.industryfinancials.com"
            ),
            unsafe_allow_html=True
        )

# App page definitions and navigation setup.
# Directory containing the view scripts for the different pages of the app.
_VIEWS = _APP_DIR / "views"

# List of pages for the app.
pages = [
    st.Page(str(_VIEWS / "comparisons.py"), title="Filtered Comparisons", icon=":material/finance_mode:", default=True),
    st.Page(str(_VIEWS / "latest_results.py"), title="Latest Results", icon=":material/calendar_today:"),
#   st.Page(str(_VIEWS / "share_repurchases.py"), title="Share Repurchases", icon=":material/paid:"),
    st.Page(str(_VIEWS / "insights.py"), title="Insights", icon=":material/emoji_objects:"),
]

# Register the pages without the sidebar nav, then render a compact link row
# below the logo so the available pages stay visible without the sidebar.
current_page = st.navigation(pages, position="hidden")

# Size each link to its label (plus room for the icon) and push the leftover
# width into a trailing spacer so the links stay grouped and compact.
nav_weights = [len(page.title) + 5 for page in pages]
nav_cols = st.columns([*nav_weights, sum(nav_weights)], gap="small")
for col, page in zip(nav_cols, pages):
    with col:
        st.page_link(page, width="stretch")

# Stock ticker setup and rendering
# Define the list of stock tickers to display, excluding defunct steelmakers.
STOCK_TICKERS = tuple(
    ticker
    for ticker in STEELMAKER_NAMES
    if ticker not in set(STEELMAKER_GROUPS.get("Defunct Steelmakers", [])) | {"ATI", "CRS"}
)
# Define the stock ticker rendering function and schedule it to run on a schedule based on market hours.
@st.fragment(run_every=_ticker_run_every())
def render_stock_ticker() -> None:
    activated = st.session_state.get("activate_stock_ticker", True)
    quotes = fetch_live_quotes(STOCK_TICKERS) if activated else {}
    st.html(
        fixed_stock_ticker_html(
            quotes,
            activated=activated,
        )
    )
# Render the stock ticker with activation controlled by the sidebar toggle.
render_stock_ticker()

current_page.run()
