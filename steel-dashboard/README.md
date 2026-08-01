# Steel Financial Dashboard

This folder contains the active steel dashboard application, data pipeline, and deployment assets.

## Architecture

The current workspace is organized as:

```
core/           Shared data pipeline and tests
   sec_pipeline/   SEC retrieval, parsing, embeddings, summarization, XBRL extraction
   scripts/        build_data.py and supporting entry points
   notebooks/      thin interactive runner
data/
   manual/         optional manual inputs and share activity files
   generated/      canonical JSON consumed by the app
quotes-api/      FastAPI service for live quotes and historical closes
streamlit-app/   active dashboard front end
deploy/          Streamlit Cloud Build config and deployment notes
```

No active `web/` front end is present in this workspace.

## Data flow

1. `core/scripts/build_data.py` builds `data/generated/financials.json` and `data/generated/buybacks.json`.
2. `core/sec_pipeline.pipeline` builds `data/generated/insights.json`.
3. `streamlit-app` reads those generated files directly.
4. `quotes-api` serves live quotes and historical close series used by the app.

## Canonical financials schema

The generated financials dataset currently includes:

- `Steelmaker`, `Year`, `Quarter`, `Period`
- `Reported End`, `AlignedYear`, `AlignedQuarter`, `AlignedPeriod`
- `Net Sales`, `Cost of Goods Sold`, `Gross Income`
- `Net Income Attributable to Stockholders`, `Earnings Per Share`
- `Long-Term Debt`, `Current Maturities`, `Total Debt`
- `Cash & Cash Equivalents`, `Short-Term Investments`, `Total Liquidity`, `Net Debt`
- `Operating Cash Flow`, `Capital Expenditures`, `Free Cash Flow`
- `Gross Margin`, `Net Margin Attributable to Stockholders`

## Quarterly alignment behavior

Quarterly peer comparisons and latest-quarter summaries use `AlignedPeriod` for comparison placement and preserve the true reported fiscal period in `Period`.

This is especially important for steelmakers with offset fiscal calendars. For example, CMC can be grouped into the same peer quarter as other issuers while still showing its true fiscal label.

## Metric sourcing

| Source | Current role |
| --- | --- |
| Auto (SEC XBRL `companyfacts`) | Primary source for the financial metrics in `financials.json` |
| Manual files in `data/manual/` | Optional overrides, cross-check fields, and share repurchase/share sale inputs |
| Derived in `build_data.py` | Gross profit, margins, debt/liquidity rollups, and free cash flow |

## Getting started

- See [core/README.md](core/README.md) for environment setup and data generation.
- See [streamlit-app/README.md](streamlit-app/README.md) for local app usage.
- See [quotes-api/README.md](quotes-api/README.md) for the quote service.
- See [deploy/README.md](deploy/README.md) for CI and deployment behavior.

## Security

Never commit secrets. Keep SEC and OpenAI credentials in `core/.env`. That file is gitignored.
