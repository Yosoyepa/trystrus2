# Witness bucket: versioned, uniform access, public access prevention.
# The app writes roots/{seq_start}-{seq_end}.json with if_generation_match=0.

resource "google_storage_bucket" "witness" {
  name     = "${local.name_prefix}-witness"
  location = upper(var.region)

  versioning {
    enabled = true
  }

  uniform_bucket_level_access = true

  public_access_prevention = "enforced"

  lifecycle_rule {
    condition {
      num_newer_versions = 10
    }
    action {
      type = "Delete"
    }
  }

  labels = local.labels

  depends_on = [google_project_service.apis]
}
