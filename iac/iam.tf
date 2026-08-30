# Three service accounts with least privilege per resource:
#   runtime — Cloud Run services (kernel, yuno_sim, merchant, web)
#   jobs    — Cloud Run Jobs (migrations, outbox-relay, sweeper)
#   deploy  — GitHub Actions via WIF (broad by necessity; documented caveat)

resource "google_service_account" "runtime" {
  account_id   = "${local.name_prefix}-runtime"
  display_name = "Aval ${var.environment} Cloud Run runtime"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "jobs" {
  account_id   = "${local.name_prefix}-jobs"
  display_name = "Aval ${var.environment} Cloud Run jobs"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "deploy" {
  account_id   = "${local.name_prefix}-deploy"
  display_name = "Aval ${var.environment} CI/CD deploy (GitHub WIF)"
  depends_on   = [google_project_service.apis]
}

# --- runtime: run services, talk to SQL via unix socket, write traces ---
resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# --- jobs: same basics, no trace agent needed ---
resource "google_project_iam_member" "jobs" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.jobs.email}"
}

# --- KMS: signer only on the evidence key (never keyring-wide admin) ---
resource "google_kms_crypto_key_iam_member" "runtime_signer" {
  crypto_key_id = google_kms_crypto_key.evidence_root.id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.runtime.email}"
}

# --- GCS: witness bucket object admin for runtime + jobs ---
resource "google_storage_bucket_iam_member" "runtime_witness" {
  bucket = google_storage_bucket.witness.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "jobs_witness" {
  bucket = google_storage_bucket.witness.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.jobs.email}"
}

# --- Secret Manager: accessor per secret, per SA (declared in secrets.tf) ---

# --- deploy SA (CI): broad but bounded to this project's deploy surface ---
resource "google_project_iam_member" "deploy" {
  for_each = toset([
    "roles/run.admin",
    "roles/cloudsql.admin",
    "roles/artifactregistry.writer",
    "roles/secretmanager.secretAccessor",
    "roles/secretmanager.viewer",
    "roles/storage.admin",
    "roles/cloudkms.admin",
    "roles/compute.loadBalancerAdmin",
    "roles/cloudscheduler.admin",
    "roles/iam.serviceAccountUser",
    "roles/monitoring.alertPolicyEditor",
    "roles/logging.configWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"
}
