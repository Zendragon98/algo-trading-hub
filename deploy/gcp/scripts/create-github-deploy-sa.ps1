# Creates github-actions-deploy SA + key for GitHub secret GCP_SA_KEY.
# Run from repo root:  .\deploy\gcp\scripts\create-github-deploy-sa.ps1

$ErrorActionPreference = "Stop"
$PROJECT = if ($env:GCP_PROJECT_ID) { $env:GCP_PROJECT_ID } else { "perfect-entry-497811-v1" }
$SA_NAME = "github-actions-deploy"
$SA_EMAIL = "${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
$KEY_FILE = Join-Path (Get-Location) "github-deploy-key.json"

$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path $gcloud)) {
  throw "gcloud not found. Install Google Cloud SDK or add it to PATH."
}

Write-Host "Project: $PROJECT"
Write-Host "Service account: $SA_EMAIL"

$saExists = $true
try {
  & $gcloud iam service-accounts describe $SA_EMAIL --project=$PROJECT 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { $saExists = $false }
} catch {
  $saExists = $false
}
if (-not $saExists) {
  & $gcloud iam service-accounts create $SA_NAME `
    --display-name="GitHub Actions GCP backend deploy" `
    --project=$PROJECT
}

$roles = @(
  "roles/cloudbuild.builds.editor",
  "roles/storage.objectAdmin",
  "roles/compute.instanceAdmin.v1",
  "roles/iap.tunnelResourceAccessor",
  "roles/compute.osAdminLogin",
  "roles/serviceusage.serviceUsageConsumer"
)
foreach ($role in $roles) {
  Write-Host "Binding $role ..."
  gcloud projects add-iam-policy-binding $PROJECT `
    --member="serviceAccount:$SA_EMAIL" `
    --role=$role `
    --condition=None | Out-Null
}

if (Test-Path $KEY_FILE) {
  Remove-Item $KEY_FILE -Force
}
gcloud iam service-accounts keys create $KEY_FILE `
  --iam-account=$SA_EMAIL `
  --project=$PROJECT

Write-Host ""
Write-Host "Created: $KEY_FILE"
Write-Host "Next:"
Write-Host "  1. GitHub repo -> Settings -> Secrets -> Actions -> New secret"
Write-Host "     Name: GCP_SA_KEY"
Write-Host "     Value: entire JSON file contents"
Write-Host "  2. Delete $KEY_FILE after saving the secret"
Write-Host "  3. Push .github/workflows/deploy-gcp-backend.yml to main"
Write-Host ""
Write-Host "See deploy/gcp/GITHUB_ACTIONS_SETUP.md"
