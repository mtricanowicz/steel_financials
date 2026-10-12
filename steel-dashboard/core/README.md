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

This writes `../data/generated/insights.json` under the aligned hierarchy
`{ticker: {aligned_year: {aligned_quarter: record}}}`. Each record contains
`reporting_period`, optional `reporting_end`, and `summary`. The hierarchy uses
aligned calendar periods while the record preserves the company's fiscal period.

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

- `Year`, `Quarter`, `Period`: aligned calendar comparison fields
- `Reporting Year`, `Reporting Quarter`, `Reporting Period`, `Reporting End`: the company's fiscal period and inspected SEC end date
- income, balance sheet, cash flow, and derived margin fields used by the dashboard

The aligned fields are used by quarterly peer views so fiscal-calendar offsets do not force issuers such as CMC into isolated latest-quarter buckets. Calendar reporters use the inspected report-end date; CMC quarterly periods are mapped one calendar quarter before the fiscal reporting period, while FY remains under its reporting year.

Fact selection requires the actual context dates to match the target fiscal
period, allowing seven days for 52/53-week reporters. CMC fiscal quarters end
in November, February, May, and August; alignment for comparisons is separate.
Fiscal labels rank only date-compatible facts, and cannot select a comparative
or wrong-quarter context. Standalone and YTD start dates are also validated;
YTD subtraction requires identical fiscal-year starts. Restatements are selected
within the eligible period. Period-end voting excludes dates outside that period.

## Metric sourcing

| Source | Current role |
| --- | --- |
| SEC XBRL company facts | Primary financial metric extraction |
| Manual files in `../data/manual/` | Optional overlap fields, share repurchases, share sales |
| Derived in `build_data.py` | Gross profit, margins, debt/liquidity rollups, free cash flow |

Operating cash flow and capital expenditures use YTD-aware extraction, as do
losses and impairment of assets and depreciation for Nucor, and combined D&A for
CMC (identified by companyfacts CIK).
Standalone Q2/Q3 facts
are preferred when available; otherwise Q2 is six-month YTD minus Q1, and Q3 is
nine-month YTD minus six-month YTD. Q4 is full year minus nine-month YTD when
available, with a full-year-minus-standalone-Q1/Q2/Q3 fallback. Missing required
inputs remain missing rather than being treated as zero during extraction.
Nucor amortization and X/STLD/CLF D&A use standalone period facts. Other issuers'
losses and impairment also use standalone period facts. For these metrics,
Q4 uses a standalone fact when available, otherwise FY minus Q1/Q2/Q3.

The data builder prefers reported combined D&A, including zero, over the sum of
depreciation and amortization. Like the cash rollup, one available component is
retained when the other is missing; if both are missing, the combined value stays
missing. Tax, losses/impairment, combined D&A, and individual depreciation and
amortization are internal inputs excluded from the final dataset; EBITDA and
interest remain visible. The current EBITDA formula treats missing add-backs as
zero, with warnings for missing inputs and partial D&A rollups. Coverage
diagnostics retain raw internal-input coverage even though those columns are
hidden from the final dataset. Accumulated depreciation is a balance-sheet balance and is not
used as period D&A expense.

Interest selection is issuer-specific: NUE uses `InterestRevenueExpenseNet`;
CLF uses `InterestIncomeExpenseNonoperatingNet`; CMC uses `InterestExpense`
with `InterestExpenseOperating` as an annual fallback; STLD uses
`InterestExpenseNonoperating` with legacy `InterestExpense` as a fallback.
Income-positive net-interest tags are negated so expense is positive and net
interest income is negative, including YTD and derived Q4 values. CMC and STLD
inputs are gross of interest income, not directly equivalent to NUE/CLF net
interest. Other issuers retain the existing candidate list.

## Tests

```powershell
cd core
pip install -e ".[dev]"
pytest
```

The unit suite covers deterministic pipeline logic such as period modeling, exhibit
provenance, weighted retrieval, guidance context allocation, summary linting,
parsing, throttling, XBRL extraction, and aligned-period behavior.
