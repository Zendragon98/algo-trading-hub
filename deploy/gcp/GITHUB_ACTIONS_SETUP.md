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
| Cloud Build permission denied / `_cloudbuild` bucket forbidden | Grant `storage.admin`, `cloudbuild.builds.builder`, `artifactregistry.writer` to `github-actions-deploy`; grant `artifactregistry.writer` to `PROJECT_NUMBER@cloudbuild.gserviceaccount.com` |
| SSH / IAP failed | VM running; firewall `algo-trading-allow-ssh-iap`; SA has `iap.tunnelResourceAccessor` + `compute.osAdminLogin` |
| `docker compose` path wrong | Ensure `/opt/algo-trading-hub/deploy/gcp` exists on VM (initial setup) |
| Build OK, old code still running | Check Actions log for “Restart engine”; run SSH step manually |

---

## Security notes

- Rotate the SA key if it was ever committed or leaked.  
- Prefer [Workload Identity Federation](https://github.com/google-github-actions/auth#workload-identity-federation) over long-lived JSON keys for production hardening (optional upgrade).
