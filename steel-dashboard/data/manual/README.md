# Manual data inputs

This folder contains optional manually curated inputs consumed by `core/scripts/build_data.py`.

The current dashboard relies primarily on SEC XBRL extraction for financial statement metrics. Manual files are used for:

- optional overlap fields that can be cross-checked against XBRL
- share repurchase history
- share sale history

## Accepted inputs

`build_data.py` prefers a single workbook when present and otherwise reads the CSV files directly:

1. `steelmaker_financial_data.xlsx` with sheets `steelmaker_financials`, `share_repurchases`, and `share_sales`, or
2. `manual_metrics.csv`, `share_repurchases.csv`, and `share_sales.csv`

## `manual_metrics.csv`

The checked-in CSV is an empty template. Its accepted columns are:

`Steelmaker,Year,Quarter,Net Sales,Cost of Goods Sold,Operating Income,Net Income,Net Income Attributable to Stockholders,Earnings Per Share,Long-Term Debt,Current Maturities,Cash & Cash Equivalents,Short-Term Investments,Interest Expense,Tax Expense,Losses & Impairment of Assets,Depreciation & Amortization,Depreciation,Amortization,Operating Cash Flow,Capital Expenditures`

`Steelmaker`, reporting `Year`, and reporting `Quarter` are the join keys. The
remaining columns are existing SEC/XBRL financial input fields also accepted
from manual files; this schema does not add new metrics. Leave unavailable
values blank. A numeric zero is an explicit value.

Select `manual` as the financial source to update without requesting SEC data.
When both `auto` and `manual` are selected, non-null XBRL values take precedence
and manual values fill missing XBRL values; discrepancies are logged. Without
overwrite, only missing stored values are filled. With overwrite, non-null
source values replace stored values for the selected reporting periods; blank
cells never erase stored data. Workbook sheet names remain
`steelmaker_financials`, `share_repurchases`, and `share_sales`.

## `share_repurchases.csv`

| Column | Description |
| --- | --- |
| Steelmaker | Ticker |
| Year | Fiscal year |
| Quarter | Period label, often `FY` |
| Shares Repurchased | Share count |
| Cost | Total cost in dollars |

## `share_sales.csv`

| Column | Description |
| --- | --- |
| Steelmaker | Ticker |
| Year | Fiscal year |
| Quarter | Period label, often `FY` |
| Shares Sold | Share count |
| Proceeds | Total proceeds in dollars |

## Build outputs influenced by this folder

- `../generated/financials.json`
- `../generated/buybacks.json`

The current canonical financial metric set remains centered on net sales, profitability, leverage/liquidity, and cash flow. Quarterly peer alignment is derived from SEC reported-end metadata and ticker-specific alignment rules in the generated dataset, not from manual files. CMC quarterly periods are mapped one calendar quarter before the fiscal reporting period.
