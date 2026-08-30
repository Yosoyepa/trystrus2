variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  default     = "southamerica-east1"
  description = "Deployment region (AGENTS.md: southamerica-east1)."
}

variable "environment" {
  type        = string
  description = "dev | prod — used in resource names and labels."

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be either dev or prod."
  }
}

variable "manage_shared_project_resources" {
  type        = bool
  description = "Exactly one state per GCP project owns shared APIs and Artifact Registry; other environments consume them without taking over their state."
}

variable "image" {
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
  description = "Backend image (kernel/yuno_sim/merchant/jobs share it via APP_MODULE)."
}

variable "web_image" {
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
  description = "SPA image (web/Dockerfile, nginx on :3000)."
}

variable "domain" {
  type        = string
  default     = ""
  description = "Custom domain for the LB + managed cert. Empty = no LB (direct run.app URLs)."
}

variable "rp_id" {
  type        = string
  default     = ""
  description = "WebAuthn RP ID. Empty derives it from domain; prod uses the apex while the API lives on a subdomain."
}

variable "rp_origin" {
  type        = string
  default     = ""
  description = "Exact browser origin allowed to complete WebAuthn ceremonies."
}

variable "yuno_sim_url" {
  type        = string
  default     = ""
  description = "Base URL the kernel uses to reach yuno_sim. Kept as a variable to avoid a kernel<->yuno resource cycle; the deploy workflow fills it with the service URL."
}

variable "rappi_bridge_url" {
  type        = string
  default     = ""
  description = "Ephemeral authenticated HTTPS tunnel to the credential-machine Rappi bridge. Empty keeps the explicit fixture fallback."
}

variable "min_instances" {
  type    = number
  default = 0
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "db_tier" {
  type        = string
  default     = "db-custom-1-3840"
  description = "Cloud SQL machine tier."
}

variable "db_availability_type" {
  type        = string
  default     = "ZONAL"
  description = "ZONAL (dev) | REGIONAL (prod)."
}

variable "deletion_protection" {
  type        = bool
  default     = false
  description = "Cloud SQL deletion protection (true in prod)."
}

variable "db_password" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Cloud SQL user password. Provide via TF_VAR_db_password; empty generates a random one (kept in state)."
}

variable "alert_email" {
  type        = string
  default     = ""
  description = "Notification email for alert policies. Empty = alerts created without a channel."
}

variable "budget_amount" {
  type        = number
  default     = 0
  description = "Monthly billing budget in USD. 0 = no budget created."
}

variable "billing_account" {
  type        = string
  default     = ""
  description = "Billing account id, required only when budget_amount > 0."
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Extra labels for billable resources."
}

locals {
  name_prefix = "aval-${var.environment}"
  labels = merge(
    var.labels,
    {
      service     = "aval"
      environment = var.environment
      managed_by  = "tofu"
    },
  )
}

variable "mcp_allowed_hosts" {
  type        = string
  default     = ""
  description = <<-EOT
    Comma-separated Host values the merchant's MCP endpoint will accept
    (DNS-rebinding protection). Empty derives it from var.domain. Set this
    explicitly when the MCP is also reached on its *.run.app hostname, which
    the LB-derived value does not cover. An entry may use a ":*" port wildcard.
  EOT
}
