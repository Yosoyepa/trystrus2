# Aval on Google Cloud Run — OpenTofu/Terraform root (flat files by domain).
# State lives in GCS; pass the per-env backend config with -backend-config.

terraform {
  required_version = "~> 1.11"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
