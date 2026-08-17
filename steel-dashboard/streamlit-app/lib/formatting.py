"""Presentation constants, labels, and formatting shared across the app."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

import pandas as pd

STEELMAKER_COLORS: dict[str, str] = {
    "NUE": "#006325",
    "STLD": "#c02e39",
    "CLF": "#002f6c",
    "CMC": "#a6ce39",
    "X": "#0058AA",
    "ATI": "#004c9d",
    "CRS": "#e40046",
}

STEELMAKER_NAMES: dict[str, str] = {
    "NUE": "Nucor",
    "STLD": "Steel Dynamics",
    "CLF": "Cleveland-Cliffs",
    "CMC": "Commercial Metals Company",
    "X": "US Steel",
    "ATI": "ATI",
    "CRS": "Carpenter Technology",
}

STEELMAKER_IR: dict[str, str] = {
    "NUE": "https://investors.nucor.com/",
    "STLD": "https://ir.steeldynamics.com/",
    "CLF": "https://www.clevelandcliffs.com/investors/",
    "CMC": "https://ir.cmc.com/",
    "X": None,
    "ATI": "https://ir.atimetals.com/",
    "CRS": "https://ir.carpentertechnology.com/",
}

STEELMAKER_LOGO_FILES: dict[str, str] = {
    "NUE": "logo_NUE.png",
    "STLD": "logo_STLD.png",
    "CLF": "logo_CLF.png",
    "CMC": "logo_CMC.png",
    "X": "logo_X.png",
    "ATI": "logo_ATI.png",
    "CRS": "logo_CRS.png",
}

STEELMAKER_GROUPS = {
    "EAF": [
        "CMC",
        "STLD",
        "NUE",
    ],
    "BOF": [
        "CLF",
        "X",
    ],
    "Defunct Steelmakers": [
        "X",
    ],
}

STEELMAKER_DEFUNCT_REASONS: dict[str, str] = {
    "X": "Acquired by Nippon Steel on June 18, 2025.",
}

# Metrics reported in dollars; displayed in millions with a currency prefix.
CURRENCY_METRICS = [
    "Net Sales",
    "Cost of Goods Sold",
    "Gross Income",
    "Net Income Attributable to Stockholders",
    "Long-Term Debt",
    "Current Maturities",
    "Total Debt",
    "Cash & Cash Equivalents",
    "Unrestricted Cash",
    "Restricted Cash",
    "Short-Term Investments",
    "Total Liquidity",
    "Net Debt",
    "Operating Cash Flow",
    "Capital Expenditures",
    "Free Cash Flow",
]

# Metrics scaled into millions for display but shown without a currency symbol.
MILLIONS_METRICS = CURRENCY_METRICS

# Dollar-denominated per-share metrics (not scaled to millions).
EPS_DOLLAR_METRICS = ["Earnings Per Share"]

# Unit metrics reported in cents.
CENTS_METRICS: list[str] = []

# Metrics reported as percentages.
PERCENT_METRICS = ["Gross Margin", "Net Margin Attributable to Stockholders"]

# Internal metrics hidden from user-facing selection and summary displays.
DISPLAY_EXCLUDED_METRICS = {
    "Operating Income",
    "Operating Margin",
    "Net Income",
    "Net Margin",
}

# User-facing metric names while preserving stable internal column keys.
METRIC_DISPLAY_NAMES: dict[str, str] = {
    "Net Income Attributable to Stockholders": "Net Earnings",
    "Net Margin Attributable to Stockholders": "Net Margin",
}

# Fast membership sets used in hot formatting/render paths.
_CURRENCY_METRIC_SET = set(CURRENCY_METRICS)
_MILLIONS_METRIC_SET = set(MILLIONS_METRICS)
_EPS_DOLLAR_METRIC_SET = set(EPS_DOLLAR_METRICS)
_CENTS_METRIC_SET = set(CENTS_METRICS)
_PERCENT_METRIC_SET = set(PERCENT_METRICS)

METRIC_GROUPS = {
    "Earnings": [
        "Net Sales",
        "Cost of Goods Sold",
        "Gross Income",
        "Net Income Attributable to Stockholders",
        "Gross Margin",
        "Net Margin Attributable to Stockholders",
        "Earnings Per Share",
    ],
    "Debt & Liquidity": [
        "Long-Term Debt",
        "Current Maturities",
        "Total Debt",
        "Cash & Cash Equivalents",
        "Short-Term Investments",
        "Total Liquidity",
        "Net Debt",
    ],
    "Cash Flow": [
        "Operating Cash Flow",
        "Capital Expenditures",
        "Free Cash Flow",
    ],
}

METRIC_DEFINITIONS: list[tuple[str, str]] = [
    ("Net Sales", "Total revenue generated from the sale of goods and services, after deducting certain sales-related adjustments such as sales returns, allowances, and discounts."),
    ("Cost of Goods Sold", "Total direct costs incurred in producing or purchasing the goods sold."),
    ("Gross Income", "Net Sales minus Cost of Goods Sold."),
    ("Net Earnings", "Portion of Net Income attributable to the company's stockholders after removing non-controlling interests."),
    ("Gross Margin", "Gross Income divided by Net Sales."),
    ("Net Margin", "Percentage of profit attributable to the company's stockholders for each dollar in revenue. Net Earnings divided by Net Sales."),
    ("Earnings Per Share", "Net income attributable to the company's stockholders allocated to each basic share outstanding."),
    ("Long-Term Debt", "Total long-term debt net of current maturities."),
    ("Current Maturities", "Portion of debt due within the next 12 months."),
    ("Total Debt", "Total debt obligations. Long-Term Debt plus Current Maturities."),
    ("Cash & Cash Equivalents", "Cash on hand plus highly liquid short-term instruments with original maturities generally under 90 days."),
    ("Short-Term Investments", "Total short-term investments."),
    ("Total Liquidity", "Cash & Cash Equivalents plus Short-Term Investments."),
    ("Net Debt", "Total Debt minus Total Liquidity."),
    ("Operating Cash Flow", "Net cash provided by operating activities during the period."),
    ("Capital Expenditures", "Cash outflows for property, equipment, and other long-lived assets."),
    ("Free Cash Flow", "Operating Cash Flow minus Capital Expenditures."),
]


def display_metric_name(metric: str) -> str:
    """Return the user-facing display name for a metric key."""
    return METRIC_DISPLAY_NAMES.get(metric, metric)


def format_metric_value(value: float | None, metric: str) -> str | None:
    """Format one scaled value for display according to its metric type.

    Currency and millions metrics are assumed to already be divided by 1e6 and
    cents metrics already multiplied by 100 by the caller.
    """
    if value is None or pd.isna(value):
        return None
    base = metric.replace(" (millions)", "")
    if base in _CURRENCY_METRIC_SET:
        sign = "-$" if value < 0 else "$"
        return f"{sign}{abs(value):,.0f}"
    if base in _EPS_DOLLAR_METRIC_SET:
        sign = "-$" if value < 0 else "$"
        return f"{sign}{abs(value):,.2f}"
    if base in _CENTS_METRIC_SET:
        return f"{value:,.2f}\u00A2"
    if base in _PERCENT_METRIC_SET:
        return f"{value:,.2f}%"
    return f"{value:,.0f}"


def scale_metric_for_display(df: pd.DataFrame, metric: str) -> tuple[pd.DataFrame, str]:
    """Scale one metric column for display and return the display column name."""
    display_col = display_metric_name(metric)
    if metric in _MILLIONS_METRIC_SET:
        df[metric] = pd.to_numeric(df[metric], errors="coerce") / 1_000_000
        display_col = f"{display_col} (millions)"
    elif metric in _CENTS_METRIC_SET:
        df[metric] = pd.to_numeric(df[metric], errors="coerce") * 100

    if display_col != metric:
        # Avoid duplicate-column collisions when aliases map to legacy names
        # (for example "Net Margin" and "Net Margin Attributable to Stockholders").
        if display_col in df.columns:
            df = df.drop(columns=[display_col])
        df.rename(columns={metric: display_col}, inplace=True)

    return df, display_col


def color_positive_negative(value: object) -> str:
    """Style helper: green for positive, red for negative, else no color."""
    if value is None:
        return ""
    try:
        numeric = float(value[:-1]) if isinstance(value, str) else float(value)
    except (ValueError, TypeError):
        return ""
    if numeric > 0:
        return "color: green"
    if numeric < 0:
        return "color: red"
    return ""


def pct_diff(base: float | None, comparison: float | None) -> float | None:
    """Signed percentage difference of ``comparison`` relative to ``base``."""
    if base is None or comparison is None or pd.isna(base) or pd.isna(comparison):
        return None
    if base == 0:
        return float("inf") if comparison != 0 else 0.0
    magnitude = round(abs((comparison - base) / base) * 100, 2)
    if base < 0 < comparison:
        return magnitude
    if base > 0 > comparison:
        return -magnitude
    if base > comparison:
        return -magnitude
    return magnitude


def get_steelmaker_logo_path(steelmaker: str) -> Path | None:
    """Return a local logo path for a steelmaker ticker, or ``None`` if missing."""
    filename = STEELMAKER_LOGO_FILES.get(steelmaker)
    if not filename:
        return None
    logo_path = Path(__file__).resolve().parents[2] / "assets" / "logos" / filename
    return logo_path if logo_path.exists() else None


def steelmaker_label_html(
    steelmaker: str,
    text: str | None = None,
    logo_height_em: float = 1.05,
    logo_before_text: bool = True,
    gap_rem: float = 0.28,
    font_weight: int | str = 400,
    font_size: str | None = None,
    logo_alignment: str = "center",
) -> str:
    """Return normal inline text with the steelmaker logo beside it."""
    display_text = text or steelmaker
    logo_path = get_steelmaker_logo_path(steelmaker)
    image_html = ""
    if logo_path is not None:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        image_html = (
            f"<img src='data:image/png;base64,{encoded}' "
            f"alt='{escape(steelmaker)} logo' "
            f"style='"
            f"height:{logo_height_em:.2f}em;"
            f"width:auto;"
            f"display:block;"
            f"object-fit:contain;"
            f"flex:0 0 auto;"
            f"'/>"
        )
    text_html = (
        f"<span style='"
        f"margin:0;"
        f"padding:0;"
        f"line-height:1.2;"
        f"font-weight:{font_weight};"
        f"{f'font-size:{font_size};' if font_size else ''}"
        f"'>"
        f"{escape(display_text)}"
        f"</span>"
    )
    if logo_before_text:
        content_html = f"{image_html}{text_html}"
    else:
        content_html = f"{text_html}{image_html}"
    return (
        f"<span style='"
        f"display:inline-flex;"
        f"align-items:{logo_alignment};"
        f"gap:{gap_rem:.2f}rem;"
        f"vertical-align:middle;"
        f"'>"
        f"{content_html}"
        f"</span>"
    )


def steelmaker_header_html(
    steelmaker: str,
    text: str,
    heading_level: int = 4,
    logo_height_em: float = 1.05,
    logo_before_text: bool = False,
    gap_rem: float = 0.28,
) -> str:
    """Return inline header HTML with a centered steelmaker logo and title text."""
    heading_level = min(max(heading_level, 1), 6)
    logo_path = get_steelmaker_logo_path(steelmaker)
    image_html = ""
    if logo_path is not None:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        image_html = (
            f"<img src='data:image/png;base64,{encoded}' "
            f"alt='{escape(steelmaker)} logo' "
            f"style='height:{logo_height_em:.2f}em;width:auto;display:block;object-fit:contain;flex:0 0 auto;'/>"
        )
    heading_tag = f"h{heading_level}"
    if logo_before_text:
        content_html = f"{image_html}<{heading_tag} style='margin:0;padding:0;line-height:1.2;'>{escape(text)}</{heading_tag}>"
    else:
        content_html = f"<{heading_tag} style='margin:0;padding:0;line-height:1.2;'>{escape(text)}</{heading_tag}>{image_html}"
    return (
        f"<div style='display:flex;align-items:center;gap:{gap_rem:.2f}rem;margin:0.05rem 0 0.12rem 0;'>"
        f"{content_html}"
        "</div>"
    )


def get_about_sidebar_html() -> str:
    """Return HTML for the About section in the sidebar."""
    return """
    <div style="font-size: 0.875rem;">
        <p>
            Explore U.S. commodity steel producer financial performance through clear and accessible
            comparisons and the latest full-year and quarterly metrics.
        </p>
        <p>
            The dashboard combines:
        </p>
        <ul>
            <li>Automatically retrieved financial metrics from SEC filings.</li>
            <li>
                Computed performance metrics derived from the sourced financial and operating data.
            </li>
        </ul>
        <p>Pages:</p>
        <ul>
            <li>
                <strong>Filtered Comparisons:</strong>
                Provides customizable views of steelmaker financial
                performance. Multiple metrics can be selected for evaluation across
                chosen steelmakers and reporting periods.
            </li>
            <li>
                <strong>Latest Results:</strong>
                Summarizes the most recent annual and quarterly results for easy viewing.
            </li>
            <li>
                <strong>Insights:</strong>
                Delivers financial, operational, and commercial insights based on
                steelmaker SEC filings. User selections retrieve content for a particular
                steelmaker and reporting period, returning a summarization generated by an
                OpenAI model.
            </li>
        </ul>
        <p>
            Unless otherwise noted, metrics are sourced directly from, or calculated
            using data reported in, steelmaker Forms 10-Q, 8-K, and 10-K filed with the
            SEC.
        </p>
    </div>
    """


def get_other_dashboard_link(
    icon_path: Path,
    name: str,
    link: str | None = None,
) -> str:
    """Return an icon and dashboard name, linked only when a URL is provided."""
    safe_name = escape(name)
    try:
        image_data = base64.b64encode(icon_path.read_bytes()).decode("ascii")
        content = (
            f'<img src="data:image/png;base64,{image_data}" '
            f'alt="{safe_name} icon" '
            f'width="24" height="24" '
            f'style="object-fit:contain;" />'
            f"<span>{safe_name}</span>"
        )
    except OSError:
        content = f"<span>{safe_name}</span>"
    shared_style = (
        "text-decoration:none;"
        "display:inline-flex;"
        "align-items:center;"
        "gap:8px;"
    )
    if link:
        safe_link = escape(link, quote=True)
        return (
            f'<a href="{safe_link}" style="{shared_style}">'
            f"{content}"
            "</a>"
        )
    return (
        f'<span style="{shared_style}">'
        f"{content}"
        "</span>"
    )


def stock_ticker_html(
    quotes: dict[str, dict],
    unavailable_message: str = "Stock prices temporarily unavailable.",
) -> str:
    """Return scrolling financial-news-style stock ticker HTML."""
    items: list[str] = []
    for ticker, quote in quotes.items():
        price = quote.get("price")
        change = quote.get("change")
        change_percent = quote.get("change_percent")
        if price is None:
            continue
        safe_ticker = escape(ticker)
        if change is None or change_percent is None:
            movement_html = ""
        else:
            change = float(change)
            change_percent = float(change_percent)
            if change > 0:
                movement_class = "stock-ticker-positive"
                arrow = "▲"
                sign = "+"
            elif change < 0:
                movement_class = "stock-ticker-negative"
                arrow = "▼"
                sign = ""
            else:
                movement_class = "stock-ticker-neutral"
                arrow = "-"
                sign = ""
            movement_html = (
                f'<span class="{movement_class}">'
                f"{arrow} {sign}{change:,.2f} "
                f"({sign}{change_percent:.2f}%)"
                "</span>"
            )
        items.append(
            f"""
            <span class="stock-ticker-item">
                <strong>{safe_ticker}</strong>
                <span>${float(price):,.2f}</span>
                {movement_html}
            </span>
            """
        )
    if items:
        content = "".join(items)
        ticker_content = f"""
            <div class="stock-ticker-track">
                <div class="stock-ticker-sequence">
                    {content}
                </div>
                <div class="stock-ticker-sequence" aria-hidden="true">
                    {content}
                </div>
            </div>
        """
    else:
        ticker_content = f"""
            <div class="stock-ticker-unavailable">
                {escape(unavailable_message)}
            </div>
        """
    return f"""
    <style>
        .stock-ticker-shell {{
            width: 100%;
            overflow: hidden;
            white-space: nowrap;
            background: #111827;
            color: #ffffff;
            border-top: 1px solid #374151;
            padding: 0.45rem 0;
            font-size: 0.875rem;
        }}
        .stock-ticker-track {{
            display: flex;
            width: max-content;
            animation: stock-ticker-scroll {4 * len(items)}s linear infinite;
        }}
        .stock-ticker-shell:hover .stock-ticker-track {{
            animation-play-state: paused;
        }}
        .stock-ticker-sequence {{
            display: inline-flex;
            align-items: center;
        }}
        .stock-ticker-item {{
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            margin-right: 2rem;
        }}
        .stock-ticker-unavailable {{
            width: 100%;
            text-align: center;
            color: #d1d5db;
        }}
        .stock-ticker-positive {{
            color: #22c55e;
        }}
        .stock-ticker-negative {{
            color: #ef4444;
        }}
        .stock-ticker-neutral {{
            color: #d1d5db;
        }}
        @keyframes stock-ticker-scroll {{
            from {{
                transform: translateX(0);
            }}

            to {{
                transform: translateX(-50%);
            }}
        }}
    </style>
    <div class="stock-ticker-shell">
        {ticker_content}
    </div>
    """


def fixed_stock_ticker_html(
    quotes: dict[str, dict],
    activated: bool,
) -> str:
    ticker_html = stock_ticker_html(quotes) if activated else ""
    return f"""
    <style>
        :root {{
            --stock-ticker-height: 2rem;
        }}
        .fixed-stock-ticker {{
            position: fixed;
            left: 0;
            right: 0;
            bottom: 0;
            width: 100vw;
            height: var(--stock-ticker-height);
            overflow: hidden;
            margin: 0;
            padding: 0;

            z-index: 50;

            visibility: {"visible" if activated else "hidden"};
            opacity: {"1" if activated else "0"};
            pointer-events: {"auto" if activated else "none"};

            transition: opacity 150ms ease;
        }}
    </style>
    <div class="fixed-stock-ticker">
        {ticker_html}
    </div>
    """
