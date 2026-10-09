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

from lib.analytics import track_page_view
from lib.data import (
    fetch_live_quotes,
    fetch_earnings_dates,
)

_APP_DIR = Path(__file__).parent
_ASSETS_DIR = _APP_DIR.parent / "assets"
_BRANDING_DIR = _ASSETS_DIR / "branding"
_MARKET_TZ = ZoneInfo("America/New_York")
# Define the list of stock tickers, excluding defunct steelmakers, to use in the crawler and earnings dates.
STOCK_TICKERS = tuple(
    ticker
    for ticker in sorted(STEELMAKER_NAMES)
    if ticker not in set(STEELMAKER_GROUPS.get("Defunct Steelmakers", [])) | {"ATI", "CRS"}
)


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


def _steelmaker_sidebar_line(steelmaker: str, earnings: dict) -> str:
    """Return one formatted steelmaker line plus additional info for the sidebar list."""
    # Define styling elements
    logo_height_em = 1.05
    gap_rem = 0.25
    # Define the label and info lines for the sidebar entries
    if steelmaker in STEELMAKER_DEFUNCT_REASONS:
        label_line = f"*{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ({steelmaker})*"
        info_line = (
                f"<span style='display:block; "
                f"margin-left:calc({logo_height_em}em + {gap_rem}rem); "
                f"margin-top:-0.2rem; line-height:1;'>"
                f"<small>*{STEELMAKER_DEFUNCT_REASONS[steelmaker]}*</small></span>"
            )
    else:
        label_line = f"{STEELMAKER_NAMES.get(steelmaker, steelmaker)} ([{steelmaker}]({STEELMAKER_IR.get(steelmaker, '#')}))"
        date_from = earnings.get("date_from")
        date_to = earnings.get("date_to")
        if date_from:
            # Map fiscal periods to calendar dates
            if steelmaker == "CMC":
                period_label = "Q1" if dt.date.fromisoformat(date_from).month <= 2 else (
                "Q2" if dt.date.fromisoformat(date_from).month <= 5 else (
                    "Q3" if dt.date.fromisoformat(date_from).month <= 8 else (
                        "Q4 and FY" if dt.date.fromisoformat(date_from).month <= 11 else "Q1"
                        )
                    )
                )
            else:
                period_label = "Q4 and FY" if dt.date.fromisoformat(date_from).month <= 3 else (
                "Q1" if dt.date.fromisoformat(date_from).month <= 6 else (
                    "Q2" if dt.date.fromisoformat(date_from).month <= 9 else "Q3"
                    )
                )
            # Dynamic handling of earnings dates that are in the future or past
            release_tense = "will be released" if dt.date.fromisoformat(date_from) >= dt.datetime.now(_MARKET_TZ).date() else "were released"
            # Formatting the date or date ranges for readability
            date_from_value = dt.date.fromisoformat(date_from)
            date_to_value = dt.date.fromisoformat(date_to) if date_to else None
            if date_to_value and date_to_value != date_from_value:
                if (date_from_value.year, date_from_value.month) == (
                    date_to_value.year,
                    date_to_value.month,
                ):
                    date_label = (
                        f"{date_from_value:%B %d}–{date_to_value:%d, %Y} (estimated)"
                    )
                elif date_from_value.year == date_to_value.year:
                    date_label = (
                        f"{date_from_value:%B %d}–"
                        f"{date_to_value:%B %d, %Y} (estimated)"
                    )
                else:
                    date_label = (
                        f"{date_from_value:%B %d, %Y}–"
                        f"{date_to_value:%B %d, %Y} (estimated)"
                    )
            else:
                date_label = date_from_value.strftime("%B %d, %Y")
            # Define the final information line
            info_line = (
                f"<span style='display:block; "
                f"margin-left:calc({logo_height_em}em + {gap_rem}rem); "
                f"margin-top:-0.2rem; line-height:1;'>"
                f"<small>{period_label} results {release_tense} on {date_label}</small></span>"
            )
        else:
            # Define the final information line
            info_line = (
                f"<span style='display:block; "
                f"margin-left:calc({logo_height_em}em + {gap_rem}rem); "
                f"margin-top:-0.2rem; line-height:1;'>"
                f"<small>Earnings release date TBA</small></span>"
            )
    # Construct the HTML label for the airline with its logo, name, and ticker.
    label = steelmaker_label_html(
        steelmaker,
        text=label_line,
        logo_height_em=logo_height_em,
        logo_before_text=True,
        gap_rem=gap_rem,
        font_size="0.875rem",
        logo_alignment="flex-start",
    )
    # Output the combined label and info line for the sidebar.
    return label + info_line


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
        earnings_dates = fetch_earnings_dates(STOCK_TICKERS)
        for group in (g for g in STEELMAKER_GROUPS if g != "Defunct Steelmakers"):
            st.markdown(f"### {group}", unsafe_allow_html=True)
            for steelmaker in STEELMAKER_GROUPS[group]:
                if steelmaker in ["ATI", "CRS"]:
                    continue
                else:
                    st.markdown(
                        _steelmaker_sidebar_line(steelmaker, earnings_dates.get(steelmaker, {})),
                        unsafe_allow_html=True,
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
    st.Page(str(_VIEWS / "comparisons.py"), title="Financial Metrics", icon=":material/finance_mode:", default=True),
    st.Page(str(_VIEWS / "insights.py"), title="Insights", icon=":material/emoji_objects:"),
    st.Page(str(_VIEWS / "latest_results.py"), title="Latest Results", icon=":material/calendar_today:"),
#   st.Page(str(_VIEWS / "share_repurchases.py"), title="Share Repurchases", icon=":material/paid:"),
]

# Register the pages without the sidebar nav, then render a compact link row
# below the logo so the available pages stay visible without the sidebar.
current_page = st.navigation(pages, position="hidden")
track_page_view(current_page.title)

# Make each page link 1/7 of the total width of the page and place the remaining width after it.
nav_weights = [1] * len(pages)
nav_cols = st.columns([*nav_weights, 7-len(pages)], gap="small")
for col, page in zip(nav_cols, pages):
    with col:
        st.page_link(page, width="stretch")

# Stock ticker setup and rendering
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
