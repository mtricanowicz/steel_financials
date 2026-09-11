"""Optional client-side analytics for logical Streamlit pages."""

from __future__ import annotations

import json
import os

import streamlit as st
import streamlit.components.v1 as components


_PAGE_PATHS = {
    "Financial Metrics": "/",
    "Insights": "/insights",
    "Latest Results": "/latest-results",
}


def track_page_view(page_title: str) -> None:
    """Send one GA4 page-view event when a visitor changes logical pages."""
    measurement_id = os.getenv("GA_MEASUREMENT_ID")
    if not measurement_id:
        return

    page_path = _PAGE_PATHS.get(page_title, "/")
    page_key = f"{page_path}:{page_title}"
    if st.session_state.get("_ga_last_page") == page_key:
        return

    st.session_state["_ga_last_page"] = page_key

    measurement_id_json = json.dumps(measurement_id)
    page_path_json = json.dumps(page_path)
    page_title_json = json.dumps(page_title)
    components.html(
        f"""
        <script>
            const analyticsWindow = window.parent;
            analyticsWindow.dataLayer = analyticsWindow.dataLayer || [];
            analyticsWindow.gtag = analyticsWindow.gtag || function() {{
                analyticsWindow.dataLayer.push(arguments);
            }};

            if (!analyticsWindow.__ga4Configured) {{
                const tag = analyticsWindow.document.createElement("script");
                tag.async = true;
                tag.src = "https://www.googletagmanager.com/gtag/js?id={measurement_id}";
                analyticsWindow.document.head.appendChild(tag);
                analyticsWindow.gtag("js", new Date());
                analyticsWindow.gtag("config", {measurement_id_json}, {{ send_page_view: false }});
                analyticsWindow.__ga4Configured = true;
            }}

            analyticsWindow.gtag("event", "page_view", {{
                page_path: {page_path_json},
                page_title: {page_title_json}
            }});
        </script>
        """,
        height=0,
        scrolling=False,
    )