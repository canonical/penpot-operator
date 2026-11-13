# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "The deployed application object."
  value       = juju_application.penpot
}

output "provides" {
  description = "Map of the provided integration endpoints."
  value = {
    grafana_dashboard = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "grafana-dashboard"
    }
    metrics_endpoint = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "metrics-endpoint"
    }
  }
}

output "requires" {
  description = "Map of the required integration endpoints."
  value = {
    ingress = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "ingress"
    }
    logging = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "logging"
    }
    oauth = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "oauth"
    }
    postgresql = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "postgresql"
    }
    redis = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "redis"
    }
    s3 = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "s3"
    }
    smtp = {
      kind     = "endpoint"
      name     = juju_application.penpot.name
      endpoint = "smtp"
    }
  }
}
