# core: SEC data pipeline

`core` contains the tested Python pipeline that builds the dashboard datasets from SEC filings and optional manual inputs.

## Responsibilities

The core layer handles two separate outputs:

1. `scripts/build_data.py` builds canonical financial and share-activity datasets.
2. `sec_pipeline.pipeline` retrieves filings and generates period-level narrative insights.

Both outputs are written to `../data/generated/` for the Streamlit app to consume directly.

## Layout

```
core/
  sec_pipeline/
    config.py        paths, environment settings, and period helpers
    edgar_client.py  rate-limited, cached SEC EDGAR client
    parse.py         HTML/PDF filing -> cleaned text
    chunk.py         text -> overlapping chunks
    embed.py         embeddings + Chroma vector store
    summarize.py     retrieval + LLM summarization
    xbrl.py          SEC companyfacts extraction and period alignment helpers
    pipeline.py      orchestrator for filing retrieval -> insights
  scripts/
    build_data.py    XBRL + manual inputs -> generated JSON
    make_sample_data.py
  notebooks/
    run_pipeline.ipynb
  tests/
    test_pipeline.py
```

## Setup

```powershell
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Set these values in `core/.env` before running networked jobs:

- `SEC_USER_AGENT`
- `OPENAI_API_KEY`

Optional:

- `EMBEDDING_BACKEND=local` for local embeddings
- `EMBEDDING_BACKEND=openai` for OpenAI embeddings

## Build financial data

From `steel-dashboard/core`:

```powershell
python -m scripts.build_data `
  --steelmakers NUE STLD CLF CMC X ATI CRS `
  --years 2019 2020 2021 2022 2023 2024 2025 2026 `
  --periods Q1 Q2 Q3 Q4 FY `
  --overwrite
```

This writes:

- `../data/generated/financials.json`
- `../data/generated/buybacks.json` when `--share-data` is used
- coverage diagnostics under `../data/generated/diagnostics/`

## Build insights

Command line:

```powershell
sec-pipeline --steelmakers NUE STLD --years 2024 --periods Q2 Q3 Q4 FY
```

Python:

```python
from sec_pipeline.pipeline import run

run(steelmakers=["NUE", "STLD"], years=[2024], periods=["Q2"])
```

This writes `../data/generated/insights.json` as `{ticker: {year: {period: markdown}}}`.

## Current financial output model

The generated financials dataset currently includes:

- `Reported End`: representative SEC end date for the row
- `AlignedYear`, `AlignedQuarter`, `AlignedPeriod`: comparison buckets derived from the nearest calendar quarter end
- income, balance sheet, cash flow, and derived margin fields used by the dashboard

The aligned-quarter fields are used by quarterly peer views so fiscal-calendar offsets do not force issuers such as CMC into isolated latest-quarter buckets.

## Metric sourcing

| Source | Current role |
| --- | --- |
| SEC XBRL company facts | Primary financial metric extraction |
| Manual files in `../data/manual/` | Optional overlap fields, share repurchases, share sales |
| Derived in `build_data.py` | Gross profit, margins, debt/liquidity rollups, free cash flow |

## Tests

```powershell
cd core
pip install -e ".[dev]"
pytest
```

The unit suite covers deterministic pipeline logic such as period modeling, parsing, throttling, XBRL extraction, and aligned-period behavior.
