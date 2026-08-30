# Cloud KMS asymmetric key for evidence root signatures (AVAL_KMS_KEY_RESOURCE).
# Asymmetric keys do NOT support rotation_period — rotate by adding a new
# version manually and publishing the new kid in JWKS (the code already
# serves current+previous).

resource "google_kms_key_ring" "evidence" {
  name       = "${local.name_prefix}-evidence"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "evidence_root" {
  name     = "evidence-root"
  key_ring = google_kms_key_ring.evidence.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm = "EC_SIGN_ED25519"
  }

  labels = local.labels

  skip_initial_version_creation = false
}
