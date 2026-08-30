# Cloud SQL Postgres 16 — public IP + unix socket connection from Cloud Run
# (cloudsql_instances), which avoids running a VPC/connector for the demo.
# Private-IP-only is the post-hackathon hardening step.

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "google_sql_database_instance" "postgres" {
  name                = "${local.name_prefix}-db"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      start_time                     = "07:00"
      point_in_time_recovery_enabled = var.deletion_protection
    }

    ip_configuration {
      ipv4_enabled = true
      authorized_networks {
        value = "0.0.0.0/0"
        name  = "placeholder-tighten-me"
      }
    }

    insights_config {
      query_insights_enabled = true
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "aval" {
  name     = "aval"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "aval" {
  name     = "aval"
  instance = google_sql_database_instance.postgres.name
  password = var.db_password != "" ? var.db_password : random_password.db.result
}
