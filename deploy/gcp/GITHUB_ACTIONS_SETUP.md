# GitHub Actions → GCP auto-deploy

On every push to **`main`** that touches the backend (see workflow `paths`), GitHub Actions will:

1. Run **Cloud Build** → push `backend:latest` to Artifact Registry  
2. **SSH** (via IAP) into `algo-trading-engine` → `docker compose pull` + `up -d`

Workflow file: [`.github/workflows/deploy-gcp-backend.yml`](../../.github/workflows/deploy-gcp-backend.yml)

---

## One-time setup

### 1. Create deploy service account (run on your PC)

**PowerShell** (repo root):

```powershell
$env:Path += ";$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin"
$PROJECT = "perfect-entry-497811-v1"
$SA_NAME = "github-actions-deploy"
$SA_EMAIL = "${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME `
  --display-name="GitHub Actions GCP backend deploy" `
  --project=$PROJECT

$roles = @(
  "roles/cloudbuild.builds.editor",
  "roles/cloudbuild.builds.builder",
  "roles/storage.admin",
  "roles/artifactregistry.writer",
  "roles/logging.logWriter",
  "roles/compute.instanceAdmin.v1",
  "roles/iap.tunnelResourceAccessor",
  "roles/compute.osAdminLogin",
  "roles/serviceusage.serviceUsageConsumer"
)
foreach ($role in $roles) {
  gcloud projects add-iam-policy-binding $PROJECT `
    --member="serviceAccount:$SA_EMAIL" `
    --role=$role `
    --condition=None
}

# Required for gcloud builds submit (default build SA is Compute Engine SA on new projects)
$PN = gcloud projects describe $PROJECT --format="value(projectNumber)"
gcloud iam service-accounts add-iam-policy-binding "${PN}-compute@developer.gserviceaccount.com" `
  --project=$PROJECT `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/iam.serviceAccountUser"

gcloud compute instances add-iam-policy-binding algo-trading-engine `
  --zone=us-central1-a `
  --project=$PROJECT `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/compute.osAdminLogin"

gcloud iam service-accounts keys create github-deploy-key.json `
  --iam-account=$SA_EMAIL `
  --project=$PROJECT
```

Keep `github-deploy-key.json` private. **Delete it** after adding to GitHub (step 2).

### 2. Add GitHub secret

1. Open your repo on GitHub → **Settings** → **Secrets and variables** → **Actions**  
2. **New repository secret**  
   - Name: `GCP_SA_KEY`  
   - Value: paste the **entire** contents of `github-deploy-key.json`  
3. Save  

```powershell
# Optional: copy JSON to clipboard (Windows)
Get-Content github-deploy-key.json | Set-Clipboard
Remove-Item github-deploy-key.json   # after secret is saved
```

### 3. Push the workflow to `main`

Merge or push `.github/workflows/deploy-gcp-backend.yml` to the **`main`** branch.

### 4. Verify

- **Actions** tab → run **Deploy backend to GCP** (or push a small `backend/` change)  
- On the VM: `sudo docker ps` should show a recent container start  

Manual run: **Actions** → **Deploy backend to GCP** → **Run workflow**.

---

## What does *not* auto-deploy

| Change | Auto-deploy? |
|--------|----------------|
| Backend code / Dockerfile | Yes (on `main`, matching paths) |
| `deploy/gcp/.env` on VM (secrets, CORS) | No — edit on VM manually |
| Frontend (Vercel) | Separate — Vercel Git integration |
| Other branches | No — only `main` (unless you edit the workflow) |

---

## Troubleshooting

| Failure | Fix |
|---------|-----|
| `GCP_SA_KEY` missing | Add secret (step 2) |
| Cloud Build fails in ~2s on “Build and push image” | Grant project roles above to `github-actions-deploy`, then grant **`roles/iam.serviceAccountUser`** on the **default build SA** (usually `PROJECT_NUMBER-compute@developer.gserviceaccount.com` — run `gcloud builds get-default-service-account`). Legacy `PROJECT_NUMBER@cloudbuild.gserviceaccount.com` only applies on older projects. |
| `_cloudbuild` bucket forbidden | Grant `storage.admin` to `github-actions-deploy`; grant `artifactregistry.writer` to the default build SA (see above) |
| SSH / IAP failed (VM step fails in ~5s) | **Do not SSH from GitHub Actions** — VM restart runs inside Cloud Build (step 3 of `cloudbuild.yaml`) using the default build SA. If that step fails: VM running; IAP TCP/22 allowed; default build SA has `iap.tunnelResourceAccessor` + `compute.osAdminLogin` on the VM instance |
| Health check failed after recreate (~4 min) | Container crash on boot — check Cloud Build log for `docker compose logs`; common causes: bad `.env`, `ENGINE_AUTOSTART=true` with invalid keys |
| `docker compose` path wrong | Ensure `/opt/algo-trading-hub/deploy/gcp` exists on VM (initial setup) |
| Build OK, old code still running | Cloud Build runs `docker compose up -d --force-recreate`; if stale, SSH to VM and run pull + recreate manually |

---

## Security notes

- Rotate the SA key if it was ever committed or leaked.  
- Prefer [Workload Identity Federation](https://github.com/google-github-actions/auth#workload-identity-federation) over long-lived JSON keys for production hardening (optional upgrade).
