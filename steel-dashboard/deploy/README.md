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

For `deploy.yml`, set these as **GitHub environment secrets** under the `production` environment (the jobs are environment-scoped).

## GitHub deploy baseline (verified)

This repository has a working GitHub Actions deploy path to Cloud Run using Workload Identity Federation.

### 1) Workload Identity provider inputs

- Workload identity pool: `github-pool`
- Provider: `github-provider`
- Provider resource (for `GCP_WORKLOAD_IDP`):
	`projects/404689453702/locations/global/workloadIdentityPools/github-pool/providers/github-provider`
- Attribute condition should allow this repo:
	`assertion.repository=='mtricanowicz/steel_financials'`

### 2) Deploy service account

- Service account: `steel-deploy-sa@steel-financial-dashboard.iam.gserviceaccount.com`
- Secret value for `GCP_DEPLOY_SA` should match exactly.

### 3) Required IAM roles for deploy SA

Grant these project-level roles to `steel-deploy-sa@steel-financial-dashboard.iam.gserviceaccount.com`:

- `roles/run.admin`
- `roles/cloudbuild.builds.editor`
- `roles/iam.serviceAccountUser`
- `roles/artifactregistry.reader`
- `roles/artifactregistry.writer`
- `roles/serviceusage.serviceUsageConsumer`
- `roles/storage.admin`
- `roles/viewer`

These roles were required for:

- `gcloud run deploy --source ...` in `quotes-api`
- `gcloud builds submit ...` and build log streaming in `streamlit`

### 4) Build context size control

`gcloud builds submit steel-dashboard` uses ignore rules from `steel-dashboard/.gcloudignore`.
Keep local caches and virtual environments excluded (especially `core/.cache` and `core/.venv`) to avoid multi-GB upload contexts.

### 5) Re-run flow

1. Trigger `Deploy` from GitHub Actions (`workflow_dispatch`, branch `main`).
2. Confirm `quotes-api` and `streamlit` jobs pass.
3. If a build log streaming error appears, verify `roles/viewer` remains present on the deploy SA.

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
