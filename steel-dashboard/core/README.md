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

## Insights retrieval and quality

Insights use weighted multi-query RAG over periodic filings and material 8-K
exhibits. For every selected 8-K, the pipeline indexes eligible `EX-99.*`
HTML/PDF attachments, including earnings releases and investor presentations.
Each chunk carries filing provenance (`form`, `accession`, `filing_date`), a
source-specific identity (`source_id`), document/exhibit fields, reporting label,
and chunk position. An exhibit has its own `source_id`, so it cannot be confused
with the short 8-K cover document.

Retrieval covers period overview, financial results, production/shipments and
utilization, labor, management, markets/capacity, commercial strategy, MD&A
explanations, operating metrics, energy/raw materials, non-GAAP results,
risk/legal disclosures, material 8-K events, and earnings-release guidance.
Query priorities are defined in `sec_pipeline.summarize.QUERY_WEIGHTS`; MD&A
explanations and forward guidance receive the highest weights.

All query results are fused before deduplication. For passage $p$:

$$
S(p) = \sum_{q \in Q_p}\frac{w_q}{60 + r_{p,q}}
$$

Repeated support across queries therefore raises relevance. Exact duplicates are
suppressed only within the same `source_id`; near-duplicates are suppressed only
for adjacent chunks from the same source. Identical language from distinct filings
remains attributable.

The context builder reserves up to two dedicated guidance-query passages before
adding the general ranked evidence. Core financial, operating, MD&A, material-event,
and guidance channels may contribute multiple passages; secondary channels are
limited to one representative. The summary prompt requires source-backed,
non-overlapping stories, separates actuals from management guidance, and ends with
a Wrap Up that recaps results before any clearly labeled guidance overview.

Generation is protected against the completion cap. If OpenAI returns
`finish_reason == "length"`, the pipeline retries once with a compact-summary
instruction that merges related facts, limits the result to ten numbered items,
and still requires a complete Wrap Up. If the compact retry also reaches the
cap, the period is rejected with an explicit error instead of persisting a
truncated summary.

### Filing periods

The pipeline preserves issuer-aware report-date matching and fiscal-year-end
inference. For Q4, the fiscal-year-aware fallback window spans the entire fourth
fiscal quarter through three months after the fiscal year end. For FY, it spans the
full fiscal year through the same post-close period. This captures annual 10-Ks and
their associated earnings-release 8-Ks for both calendar and offset fiscal-year
issuers. `PeriodSpec.date_window()` remains a calendar compatibility fallback;
`PeriodSpec.period_end()` represents the actual reporting end and prompts use the
issuer-specific report date when available.

### Lint summaries

Use the quality lint before and after a pilot or broader regeneration:

```powershell
lint-summaries ../data/generated/insights.json --discover
lint-summaries ../data/generated/insights.json --baseline old-insights.json --compare new-insights.json
```

It emits JSON with per-summary and aggregate word/item counts, model-voice phrase
hits, figure density, bold/body overlap, causal-attribution signals, truncation,
repeated metric families, secondary-section padding, Wrap Up figure reuse, and
cross-summary four-gram reuse. Discovery mode lists reused openers and phrases by
issuer spread. These are review signals, not proof of factual accuracy.

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

The unit suite covers deterministic pipeline logic such as period modeling, exhibit
provenance, weighted retrieval, guidance context allocation, summary linting,
parsing, throttling, XBRL extraction, and aligned-period behavior.
