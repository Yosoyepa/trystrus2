output "kernel_url" {
  value       = google_cloud_run_v2_service.kernel.uri
  description = "Kernel service URL (run.app direct; use the LB host when domain is set)."
}

output "yuno_sim_url" {
  value = google_cloud_run_v2_service.yuno_sim.uri
}

output "merchant_url" {
  value = google_cloud_run_v2_service.merchant.uri
}

output "web_url" {
  value = google_cloud_run_v2_service.web.uri
}

output "lb_ip" {
  value       = var.domain != "" ? google_compute_global_address.lb[0].address : null
  description = "Global anycast IP for the LB (create an A record for var.domain)."
}

output "db_connection_name" {
  value       = local.cloudsql_connection
  description = "Cloud SQL connection name (unix socket path base /cloudsql/<this>)."
}

output "db_password_note" {
  value       = "DB password is in state (random_password.db) — rotate with gcloud and update the ${local.name_prefix}-db-url secret."
  description = "Reminder: keep the password only in Secret Manager going forward."
}

output "kms_key_resource" {
  value       = local.kms_key_resource
  description = "Value for AVAL_KMS_KEY_RESOURCE."
}

output "witness_bucket" {
  value       = local.witness_bucket
  description = "Value for AVAL_WITNESS_BUCKET."
}

output "artifact_registry" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/aval"
  description = "Image base for CI/CD pushes."
}
