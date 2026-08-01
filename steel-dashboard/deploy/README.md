# Deployment

The active deploy path in this workspace targets Google Cloud Run for both services.

## Active components

| Component | Path | Hosting |
| --- | --- | --- |
| Quotes API | `quotes-api/` | Cloud Run |
| Streamlit app | `streamlit-app/` | Cloud Run |
| Data build + insights | `core/` | GitHub Actions jobs |

No active `web/` application directory is present in this workspace. The GitHub workflows still contain conditional `web` jobs that are skipped unless `steel-dashboard/web/package-lock.json` exists.

## Workflow behavior

The repository-level workflows under `.github/workflows/` assume the repo root contains `steel-dashboard/`.

| Workflow | Trigger | Current purpose |
| --- | --- | --- |
| `ci.yml` | push / PR | Run core tests. Optionally run guarded web build steps if a web app is present. |
| `deploy.yml` | manual / after successful CI on `main` | Deploy `quotes-api` and `streamlit-app`. Optionally deploy a guarded web app if present. |
| `refresh-data.yml` | quarterly cron / manual | Rebuild generated datasets and insights, upload artifacts, then redeploy Streamlit. Optionally rebuild a guarded web app if present. |

## Required secrets

| Secret | Used by | Description |
| --- | --- | --- |
| `GCP_PROJECT_ID` | deploy, refresh | Google Cloud project id. |
| `GCP_WORKLOAD_IDP` | deploy, refresh | Workload Identity Federation provider resource. |
| `GCP_DEPLOY_SA` | deploy, refresh | Deploy service account email. |
| `QUOTES_API_URL` | deploy, refresh | Public URL of the deployed quotes-api, passed into Streamlit deploys. |
| `SEC_USER_AGENT` | refresh | Contact string for SEC EDGAR requests. |
| `OPENAI_API_KEY` | refresh | Insight summarization and OpenAI embedding key. |
| `FIREBASE_SERVICE_ACCOUNT` | optional | Only needed if the guarded `web` deployment path is reactivated. |

## Manual deploy

From `steel-dashboard/`:

```powershell
# 1. Deploy quotes-api
gcloud run deploy quotes-api --source quotes-api --region us-central1 --allow-unauthenticated

# 2. Build and deploy Streamlit
$env:QUOTES_API_URL = "https://quotes-xxxx.run.app"
$env:STREAMLIT_TAG = "manual-001"
gcloud builds submit . --config deploy/cloudbuild.streamlit.yaml --substitutions=SHORT_SHA="$env:STREAMLIT_TAG",_QUOTES_API_URL="$env:QUOTES_API_URL"
```

`deploy/cloudbuild.streamlit.yaml` builds from the `steel-dashboard/` root so generated data is included in the image build context.

## Data refresh

From `steel-dashboard/core`:

```powershell
pip install -e .
python -m scripts.build_data --steelmakers NUE STLD CLF CMC X ATI CRS --years 2026 --periods Q2
python -m sec_pipeline.pipeline --steelmakers NUE STLD CLF CMC X ATI CRS --years 2026 --periods Q2
```

Set `SEC_USER_AGENT` and `OPENAI_API_KEY` in `core/.env` first.
