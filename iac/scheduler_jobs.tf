# Cloud Scheduler every 60s (the minimum) → Cloud Run Jobs.
# The 120s escalation fail-closed deadline is what forces this cadence.

resource "google_cloud_scheduler_job" "relay_tick" {
  name             = "${local.name_prefix}-relay-tick"
  region           = var.region
  schedule         = "* * * * *"
  time_zone        = "America/Bogota"
  attempt_deadline = "320s"
  paused           = false

  retry_config {
    retry_count = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.outbox_relay.name}:run"

    oidc_token {
      service_account_email = google_service_account.jobs.email
    }

    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode("{}")
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_scheduler_job" "sweeper_tick" {
  name             = "${local.name_prefix}-sweeper-tick"
  region           = var.region
  schedule         = "* * * * *"
  time_zone        = "America/Bogota"
  attempt_deadline = "120s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.sweeper.name}:run"

    oidc_token {
      service_account_email = google_service_account.jobs.email
    }

    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode("{}")
  }

  depends_on = [google_project_service.apis]
}
