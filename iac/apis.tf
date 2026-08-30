# Enable every API the stack needs. disable_on_destroy=false so a destroy of
# this root never disables shared project services underneath the team.

locals {
  apis = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "cloudbilling.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = var.manage_shared_project_resources ? toset(local.apis) : toset([])

  service            = each.value
  disable_on_destroy = false
}
