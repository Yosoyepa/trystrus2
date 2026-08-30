# Alerting: p95 latency, 5xx rate, Cloud SQL CPU. Log-based outbox backlog
# alert needs a logging metric fed by the relay job (post-merge TODO).

resource "google_monitoring_alert_policy" "p95_latency" {
  display_name = "Aval ${var.environment} — p95 latency > 5s"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run p95 request latency"

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_latencies\" resource.type=\"cloud_run_revision\" resource.label.service_name=starts_with(\"${local.name_prefix}\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 5000
      duration        = "300s"
      trigger {
        count = 1
      }

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MEAN"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }
}

resource "google_monitoring_alert_policy" "error_5xx" {
  display_name = "Aval ${var.environment} — 5xx burst"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx count"

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" metric.label.response_code_class=\"500\" resource.label.service_name=starts_with(\"${local.name_prefix}\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"
      trigger {
        count = 1
      }

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "sql_cpu" {
  display_name = "Aval ${var.environment} — Cloud SQL CPU > 80%"
  combiner     = "OR"

  conditions {
    display_name = "Cloud SQL CPU utilization"

    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/total_utilization\" resource.type=\"cloudsql_database\" resource.label.database_id=\"${var.project_id}:${google_sql_database_instance.postgres.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "300s"
      trigger {
        count = 1
      }

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
}

# Optional monthly budget (needs var.billing_account and var.budget_amount).
resource "google_billing_budget" "monthly" {
  count = var.budget_amount > 0 ? 1 : 0

  billing_account = var.billing_account
  display_name    = "Aval ${var.environment} monthly budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}
