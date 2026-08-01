# streamlit-app

This is the active dashboard front end for the steel financial workspace.

## App structure

The multipage app is registered in [app.py](app.py) and currently exposes:

| Page | Source | Description |
| --- | --- | --- |
| Filtered Comparisons | `views/comparisons.py` | Compare selected metrics across steelmakers and periods with tables, line charts, and peer-difference bars. |
| Latest Results | `views/latest_results.py` | Latest quarterly and full-year snapshots across selected steelmakers. |
| Insights | `views/insights.py` | Precomputed filing-based narrative insights. |

`share_repurchases.py` remains in the tree and in smoke tests, but it is not currently linked in the top-level page navigation.

## Data model

The app reads precomputed files from `../data/generated/` behind cached loaders:

- `financials.json`
- `buybacks.json`
- `insights.json`

The app does not scrape SEC data or rebuild financials at request time.

## Quarterly alignment behavior

Quarterly peer views use aligned reporting buckets:

1. `AlignedPeriod` controls comparison placement and latest-quarter selection.
2. `Period` remains the true reported fiscal period.

Current UI behavior:

- line charts plot quarterly points on `AlignedPeriod`
- extra hover context appears only when a steelmaker's fiscal period differs from the aligned comparison period
- latest quarterly summary headers show each steelmaker's true reported `YYYYQX` period when available

Full-year views continue to use the true reported fiscal periods directly.

## Run locally

First build data from [../core](../core), then:

```powershell
cd streamlit-app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `DASHBOARD_DATA_DIR` | `../data/generated` | Directory containing generated JSON datasets. |
| `QUOTES_API_URL` | `http://localhost:8080` | Base URL for the live quote service. |

## Validation

The app includes a headless smoke test:

```powershell
python smoke_test.py
```

## Container

Build from the `steel-dashboard` root so generated data is in the Docker context:

```powershell
docker build -f streamlit-app/Dockerfile -t steel-streamlit .
docker run -p 8080:8080 -e QUOTES_API_URL=https://quotes.example.run.app steel-streamlit
```
