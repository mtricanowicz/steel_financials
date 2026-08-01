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

Quotes are cached by trading day. Historical responses are cached per request shape.

## Local development

```powershell
cd quotes-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Then open `http://localhost:8080/docs`.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `8080` | Bind port, typically supplied by Cloud Run. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins. |

## Container

```powershell
docker build -t quotes-api .
docker run -p 8080:8080 -e ALLOWED_ORIGINS=https://your-dashboard.example quotes-api
```

The image is ready for Cloud Run deployment.
