# Steel Financial Dashboard

This repository contains the steel financial dashboard workspace. The active application and data pipeline live under [steel-dashboard](steel-dashboard).

## Overview

The current workspace is organized around a precomputed data model:

1. [steel-dashboard/core](steel-dashboard/core) retrieves SEC data, extracts XBRL financials, and generates filing insights.
2. [steel-dashboard/data/generated](steel-dashboard/data/generated) stores the canonical JSON consumed by the app.
3. [steel-dashboard/streamlit-app](steel-dashboard/streamlit-app) renders the dashboard.
4. [steel-dashboard/quotes-api](steel-dashboard/quotes-api) provides live stock quotes and historical close series.

The configured issuer universe and automation examples center on:

- NUE
- STLD
- CLF
- CMC
- X
- ATI
- CRS

## Current metric model

The canonical financials export currently includes:

- Period fields: `Year`, `Quarter`, `Period`, `Reported End`, `AlignedYear`, `AlignedQuarter`, `AlignedPeriod`
- Income metrics: `Net Sales`, `Cost of Goods Sold`, `Gross Income`, `Net Income Attributable to Stockholders`, `Earnings Per Share`
- Balance sheet metrics: `Long-Term Debt`, `Current Maturities`, `Total Debt`, `Cash & Cash Equivalents`, `Short-Term Investments`, `Total Liquidity`, `Net Debt`
- Cash flow metrics: `Operating Cash Flow`, `Capital Expenditures`, `Free Cash Flow`
- Derived margins: `Gross Margin`, `Net Margin Attributable to Stockholders`

## Quarterly alignment

Quarterly peer views use two period concepts:

1. `Period` is the steelmaker's true reported fiscal period.
2. `AlignedPeriod` is the nearest calendar-quarter comparison bucket derived from the reported end date.

This allows companies with offset fiscal calendars, such as CMC, to line up with peers by reporting timeframe without losing the original fiscal label.

## Deployment

The active deployable components in this workspace are:

- Cloud Run for [steel-dashboard/streamlit-app](steel-dashboard/streamlit-app)
- Cloud Run for [steel-dashboard/quotes-api](steel-dashboard/quotes-api)
- GitHub Actions workflows in [.github/workflows](.github/workflows) for CI, deploy, and data refresh

Some workflows still contain optional guarded `web/` steps for a static front end, but no active `steel-dashboard/web` directory exists in this workspace.

## Repo guides

Start with:

- [steel-dashboard/README.md](steel-dashboard/README.md)
- [steel-dashboard/core/README.md](steel-dashboard/core/README.md)
- [steel-dashboard/streamlit-app/README.md](steel-dashboard/streamlit-app/README.md)
- [steel-dashboard/quotes-api/README.md](steel-dashboard/quotes-api/README.md)
- [steel-dashboard/deploy/README.md](steel-dashboard/deploy/README.md)
- [steel-dashboard/data/manual/README.md](steel-dashboard/data/manual/README.md)

## Sources

- NUE: https://investors.nucor.com/
- STLD: https://ir.steeldynamics.com/
- CLF: https://www.clevelandcliffs.com/investors/
- CMC: https://ir.cmc.com/
- X: https://www.ussteel.com/about-us/financial-information/
- ATI: https://ir.atimetals.com/
- CRS: https://ir.carpentertechnology.com/

Created by Michael Tricanowicz.