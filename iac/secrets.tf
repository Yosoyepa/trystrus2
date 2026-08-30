# Secret Manager. Initial versions are placeholders so the first apply does
# not fail; real values are added with `gcloud secrets versions add` during
# bootstrap (never in tfvars, never in git).

locals {
  secrets = {
    issuer_pem     = "aval-issuer-ed25519"
    merchant_pem   = "aval-merchant-es256"
    yuno_webhook   = "aval-yuno-webhook-ed25519"
    db_url         = "${local.name_prefix}-db-url"
    db_url_async   = "${local.name_prefix}-db-url-async"
    idem_secret    = "${local.name_prefix}-idem-secret"
    llm_openai_key = "${local.name_prefix}-llm-openai-key"
    llm_gemini_key = "${local.name_prefix}-llm-gemini-key"
  }
}

resource "google_secret_manager_secret" "secrets" {
  for_each = local.secrets

  secret_id = each.value
  labels    = local.labels

  replication {
    auto {
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "placeholders" {
  for_each = local.secrets

  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = "REPLACE_ME"
}

# Least-privilege accessors: runtime + jobs can read, nothing else.
resource "google_secret_manager_secret_iam_member" "runtime_accessors" {
  for_each = local.secrets

  secret_id = google_secret_manager_secret.secrets[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "jobs_accessors" {
  for_each = local.secrets

  secret_id = google_secret_manager_secret.secrets[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.jobs.email}"
}
