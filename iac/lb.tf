# Global External Application LB: 4 serverless NEGs → backend services,
# Cloud Armor (preconfigured OWASP rules + rate limit) on the API backends,
# url_map replicating the local nginx contract (/api/*, /yuno/*, /merchant/*
# with prefix strip; default → SPA), managed cert + forwarding rule only when
# var.domain is set. Without a domain, dev runs on direct run.app URLs.

resource "google_compute_region_network_endpoint_group" "kernel" {
  name                  = "${local.name_prefix}-kernel-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.kernel.name
  }
  depends_on = [google_project_service.apis]
}

resource "google_compute_region_network_endpoint_group" "yuno_sim" {
  name                  = "${local.name_prefix}-yuno-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.yuno_sim.name
  }
  depends_on = [google_project_service.apis]
}

resource "google_compute_region_network_endpoint_group" "merchant" {
  name                  = "${local.name_prefix}-merchant-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.merchant.name
  }
  depends_on = [google_project_service.apis]
}

resource "google_compute_region_network_endpoint_group" "web" {
  name                  = "${local.name_prefix}-web-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.web.name
  }
  depends_on = [google_project_service.apis]
}

# --- Cloud Armor: WAF preconfigured rules + per-IP rate limit ---
resource "google_compute_security_policy" "api_armor" {
  name        = "${local.name_prefix}-api-armor"
  description = "OWASP preconfigured WAF + rate limit for Aval APIs"

  # OWASP top-5 preconfigured rules (sqli, xss, lfi, rce, scanner)
  rule {
    action      = "deny(403)"
    priority    = 1000
    description = "SQLi"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
    preview = true
  }

  rule {
    action      = "deny(403)"
    priority    = 1010
    description = "XSS"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
    preview = true
  }

  rule {
    action      = "deny(403)"
    priority    = 1020
    description = "LFI"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1})"
      }
    }
    preview = true
  }

  rule {
    action      = "deny(403)"
    priority    = 1030
    description = "RCE"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('rce-v33-stable', {'sensitivity': 1})"
      }
    }
    preview = true
  }

  rule {
    action      = "rate_based_ban"
    priority    = 2000
    description = "Rate limit 100 req/min per IP"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "default allow"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# --- backend services ---
resource "google_compute_backend_service" "kernel" {
  name                  = "${local.name_prefix}-kernel-be"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.api_armor.id
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.kernel.id
  }
}

resource "google_compute_backend_service" "yuno_sim" {
  name                  = "${local.name_prefix}-yuno-be"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.api_armor.id
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.yuno_sim.id
  }
}

resource "google_compute_backend_service" "merchant" {
  name                  = "${local.name_prefix}-merchant-be"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.api_armor.id
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.merchant.id
  }
}

resource "google_compute_backend_service" "web" {
  name                  = "${local.name_prefix}-web-be"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = true

  backend {
    group = google_compute_region_network_endpoint_group.web.id
  }
}

# --- url map: replicate the compose nginx contract ---
resource "google_compute_url_map" "main" {
  name            = "${local.name_prefix}-url-map"
  default_service = google_compute_backend_service.web.id

  host_rule {
    hosts        = ["*"]
    path_matcher = "main"
  }

  path_matcher {
    name            = "main"
    default_service = google_compute_backend_service.web.id

    route_rules {
      priority = 1
      service  = google_compute_backend_service.kernel.id

      match_rules {
        prefix_match = "/api/"
      }

      route_action {
        url_rewrite {
          path_prefix_rewrite = "/"
        }
      }
    }

    route_rules {
      priority = 2
      service  = google_compute_backend_service.yuno_sim.id

      match_rules {
        prefix_match = "/yuno/"
      }

      route_action {
        url_rewrite {
          path_prefix_rewrite = "/"
        }
      }
    }

    route_rules {
      priority = 3
      service  = google_compute_backend_service.merchant.id

      match_rules {
        prefix_match = "/merchant/"
      }

      route_action {
        url_rewrite {
          path_prefix_rewrite = "/"
        }
      }
    }
  }
}

# --- TLS + IP only with a real domain (managed certs need one) ---
resource "google_compute_managed_ssl_certificate" "main" {
  count = var.domain != "" ? 1 : 0

  name        = "${local.name_prefix}-cert"
  description = "Google-managed cert for ${var.domain}"

  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "main" {
  count = var.domain != "" ? 1 : 0

  name             = "${local.name_prefix}-https-proxy"
  url_map          = google_compute_url_map.main.id
  ssl_certificates = [google_compute_managed_ssl_certificate.main[0].id]
}

resource "google_compute_global_address" "lb" {
  count = var.domain != "" ? 1 : 0

  name = "${local.name_prefix}-lb-ip"
}

resource "google_compute_global_forwarding_rule" "https" {
  count = var.domain != "" ? 1 : 0

  name                  = "${local.name_prefix}-https"
  target                = google_compute_target_https_proxy.main[0].id
  ip_address            = google_compute_global_address.lb[0].id
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
