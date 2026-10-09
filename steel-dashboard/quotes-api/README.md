# quotes-api

This FastAPI service provides the only live-request data used by the dashboard:

- current quote snapshots
- aligned daily close history

All financial statement data is precomputed and read directly from generated JSON by the Streamlit app.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe returning `{"status": "ok"}`. |
| GET | `/quotes?tickers=NUE,STLD,CMC,CLF` | Last close, day change, and change percent per ticker. |
| GET | `/history?tickers=NUE,STLD&start=2024-01-01` | Daily close history aligned on a shared date axis. |
| GET | `/earnings?tickers=NUE,STLD,CMC,CLF` | Next earnings date (or estimated date range) per ticker. |

Quotes are cached by trading day. Historical responses are cached per request shape.

Earnings calendars are cached in-memory per ticker for 24 hours from the fetch
(not reset at midnight). Each fetch retries a few times; failures are never
cached, an empty calendar is cached for only an hour, and the last known date is
served if a refresh comes back empty before that date passes. Dates come
from Yahoo Finance via yfinance and may be estimates, missing, or returned as a
range; they are not guaranteed confirmed release dates. For example:

```json
{
  "earnings": [
    {
      "ticker": "AAL",
      "date_from": "2026-10-22",
      "date_to": "2026-10-23",
      "error": null
    }
  ]
}
```

If no upcoming date is available, `date_from` and `date_to` are `null` and
`error` is `"no upcoming date"`. Provider failures return `"fetch failed"` and
are not cached, allowing a later request to retry.

## Local development

```
powershell
cd quotes-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Then open `http://localhost:8080/docs`.

To activate the API, run the following command from the repo root in Powershell:
```
Set-Location ".\steel-dashboard\quotes-api"
python -m uvicorn main:app --reload --port 8080
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `8080` | Bind port, typically supplied by Cloud Run. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins. |

## Container

```
powershell
docker build -t quotes-api .
docker run -p 8080:8080 -e ALLOWED_ORIGINS=https://your-dashboard.example quotes-api
```

The image is ready for Cloud Run deployment.
