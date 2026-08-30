# Artifact Registry + 4 Cloud Run services + 3 Cloud Run Jobs.
# One backend image (var.image) covers kernel / yuno_sim / merchant / jobs via
# APP_MODULE; the SPA has its own image (web/Dockerfile, nginx on :3000).

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "aval"
  format        = "DOCKER"
  description   = "Aval backend and web images"
  depends_on    = [google_project_service.apis]
}

locals {
  cloudsql_connection = "${var.project_id}:${var.region}:${google_sql_database_instance.postgres.name}"
  witness_bucket      = google_storage_bucket.witness.name
  kms_key_resource    = google_kms_crypto_key.evidence_root.id
  db_url_secret       = "${local.name_prefix}-db-url:latest"
  db_url_async_secret = "${local.name_prefix}-db-url-async:latest"
  idem_secret         = "${local.name_prefix}-idem-secret:latest"
  llm_openai_secret   = "${local.name_prefix}-llm-openai-key:latest"
  llm_gemini_secret   = "${local.name_prefix}-llm-gemini-key:latest"

  # Passkeys require a registrable domain: *.run.app is on the Public Suffix
  # List, so rp_id must be the real domain in prod (ADR-018).
  rp_id     = var.domain != "" ? var.domain : "localhost"
  rp_origin = var.domain != "" ? "https://${var.domain}" : "http://localhost:3000"
}

# --- kernel (FastAPI: mandates, decision, audit, evidence, agent bridge) ---
resource "google_cloud_run_v2_service" "kernel" {
  name     = "${local.name_prefix}-kernel"
  location = var.region
  ingress  = var.domain != "" ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  lifecycle {
    # GitHub Actions owns immutable image promotion; IaC owns service shape.
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "300s"
    max_instance_request_concurrency = 20 # CR-05: async handlers block on sync I/O

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.cloudsql_connection]
      }
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }

      env {
        name  = "APP_MODULE"
        value = "src.api.main:app"
      }
      env {
        name  = "AVAL_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "AVAL_RP_ID"
        value = local.rp_id
      }
      env {
        name  = "AVAL_RP_ORIGIN"
        value = local.rp_origin
      }
      env {
        name  = "AVAL_WITNESS_BUCKET"
        value = local.witness_bucket
      }
      env {
        name  = "AVAL_KMS_KEY_RESOURCE"
        value = local.kms_key_resource
      }
      env {
        name  = "AVAL_YUNO_SIM_URL"
        value = var.yuno_sim_url
      }
      env {
        name  = "LLM_PROVIDER"
        value = "gemini"
      }
      env {
        name  = "LLM_MODEL"
        value = "gemini-3.7-flash"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-db-url"
            version = "latest"
          }
        }
      }
      env {
        name = "AVAL_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-db-url-async"
            version = "latest"
          }
        }
      }
      env {
        name = "AVAL_IDEM_SECRET"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-idem-secret"
            version = "latest"
          }
        }
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-llm-gemini-key"
            version = "latest"
          }
        }
      }
      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-llm-openai-key"
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        period_seconds        = 2
        failure_threshold     = 15
        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_sql_database_instance.postgres,
    google_secret_manager_secret_version.placeholders,
  ]
}

# --- yuno_sim (AP2 rail simulation) — internal only when LB exists ---
resource "google_cloud_run_v2_service" "yuno_sim" {
  name     = "${local.name_prefix}-yuno-sim"
  location = var.region
  ingress  = var.domain != "" ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.runtime.email
    timeout         = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.cloudsql_connection]
      }
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }

      env {
        name  = "APP_MODULE"
        value = "src.yuno_sim.main:app"
      }
      env {
        name  = "YUNO_ISSUER_URL"
        value = var.domain != "" ? "https://${var.domain}/api" : google_cloud_run_v2_service.kernel.uri
      }
      env {
        name = "YUNO_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-db-url"
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        period_seconds    = 2
        failure_threshold = 15
        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# --- merchant (VuelaYa catalog + AP2 checkout) ---
resource "google_cloud_run_v2_service" "merchant" {
  name     = "${local.name_prefix}-merchant"
  location = var.region
  ingress  = var.domain != "" ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.runtime.email
    timeout         = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.cloudsql_connection]
      }
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }

      env {
        name  = "APP_MODULE"
        value = "src.merchant.main:app"
      }
      env {
        name  = "MERCHANT_KERNEL_URL"
        value = var.domain != "" ? "https://${var.domain}/api" : google_cloud_run_v2_service.kernel.uri
      }
      env {
        name  = "MERCHANT_YUNO_SIM_URL"
        value = var.domain != "" ? "https://${var.domain}/yuno" : google_cloud_run_v2_service.yuno_sim.uri
      }
      env {
        name  = "MERCHANT_FIXTURES_DIR"
        value = "/app/aval/contracts/fixtures"
      }
      env {
        name = "MERCHANT_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = "${local.name_prefix}-db-url-async"
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        period_seconds    = 2
        failure_threshold = 15
        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# --- web (SPA behind nginx; serves static + falls back per env) ---
resource "google_cloud_run_v2_service" "web" {
  name     = "${local.name_prefix}-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.runtime.email
    timeout         = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.web_image
      ports {
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      startup_probe {
        period_seconds    = 2
        failure_threshold = 15
        http_get {
          path = "/"
          port = 3000
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# --- Jobs ---

resource "google_cloud_run_v2_job" "migrations" {
  name     = "${local.name_prefix}-migrations"
  location = var.region
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    template {
      service_account = google_service_account.jobs.email
      timeout         = "300s"

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.cloudsql_connection]
        }
      }

      containers {
        image   = var.image
        command = ["alembic", "upgrade", "head"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = "${local.name_prefix}-db-url"
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job" "outbox_relay" {
  name     = "${local.name_prefix}-outbox-relay"
  location = var.region
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    template {
      service_account = google_service_account.jobs.email
      timeout         = "300s"
      max_retries     = 3

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.cloudsql_connection]
        }
      }

      containers {
        image   = var.image
        command = ["python", "-m", "src.api.jobs.relay"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = "${local.name_prefix}-db-url"
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job" "sweeper" {
  name     = "${local.name_prefix}-sweeper"
  location = var.region
  labels   = local.labels

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    template {
      service_account = google_service_account.jobs.email
      timeout         = "120s"
      max_retries     = 2

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.cloudsql_connection]
        }
      }

      containers {
        image   = var.image
        command = ["python", "-m", "src.agent.cli", "tick"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = "${local.name_prefix}-db-url"
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# IAM: let the LB's allUsers check reach services only via the LB path in dev.
# The NEG pattern requires the backend service SA; keep runtime invoker on
# jobs so Scheduler (with the jobs SA) can execute them.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker_relay" {
  name     = google_cloud_run_v2_job.outbox_relay.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.jobs.email}"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker_sweeper" {
  name     = google_cloud_run_v2_job.sweeper.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.jobs.email}"
}

# Public invoker permissions for direct Cloud Run access in dev
resource "google_cloud_run_v2_service_iam_member" "public_kernel" {
  name     = google_cloud_run_v2_service.kernel.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "public_merchant" {
  name     = google_cloud_run_v2_service.merchant.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "public_yuno_sim" {
  name     = google_cloud_run_v2_service.yuno_sim.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "public_web" {
  name     = google_cloud_run_v2_service.web.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
