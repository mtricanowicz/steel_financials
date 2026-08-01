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

The current checked-in CSV header is:

`Steelmaker,Year,Quarter,Passenger Revenue,RPM,ASM,Profit Sharing,Long-Term Debt,Operating Revenue,Operating Expenses,Net Income`

In the current steel dashboard:

- `Steelmaker`, `Year`, and `Quarter` are the join keys
- overlap columns such as `Long-Term Debt` and `Net Income` can be compared against XBRL values during the build
- legacy columns such as `Passenger Revenue`, `RPM`, `ASM`, and `Profit Sharing` may still appear in historical workbook exports, but they are not part of the current primary steel dashboard metric set

If an overlap field is provided and the XBRL value is missing, the manual value can fill the gap. If both are present and materially different, the build logs a mismatch.

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

The current canonical financial metric set remains centered on net sales, profitability, leverage/liquidity, and cash flow. Quarterly peer alignment is derived from SEC reported end dates in the generated dataset, not from manual files.
