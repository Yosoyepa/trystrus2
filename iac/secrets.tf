# Secret Manager. Initial versions are placeholders so the first apply does
# not fail; real values are added with `gcloud secrets versions add` during
# bootstrap (never in tfvars, never in git).

locals {
  # Keep the already-provisioned dev IDs stable. Production gets independent
  # signing identities so a compromised dev runtime cannot mint prod tokens.
  signing_secrets = {
    issuer_pem   = var.environment == "dev" ? "aval-issuer-ed25519" : "${local.name_prefix}-issuer-ed25519"
    merchant_pem = var.environment == "dev" ? "aval-merchant-es256" : "${local.name_prefix}-merchant-es256"
    yuno_webhook = var.environment == "dev" ? "aval-yuno-webhook-ed25519" : "${local.name_prefix}-yuno-webhook-ed25519"
  }

  environment_secrets = {
    db_url                  = "${local.name_prefix}-db-url"
    db_url_async            = "${local.name_prefix}-db-url-async"
    idem_secret             = "${local.name_prefix}-idem-secret"
    llm_openai_key          = "${local.name_prefix}-llm-openai-key"
    llm_gemini_key          = "${local.name_prefix}-llm-gemini-key"
    rappi_bridge_token      = "${local.name_prefix}-rappi-bridge-token"
    telegram_bot_token      = "${local.name_prefix}-telegram-bot-token"
    telegram_webhook_secret = "${local.name_prefix}-telegram-webhook-secret"
  }

  secrets = merge(local.signing_secrets, local.environment_secrets)
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
