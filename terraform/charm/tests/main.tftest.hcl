# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variables {
  app_name    = "penpot"
  base        = "ubuntu@24.04"
  channel     = "latest/edge"
  config      = {}
  constraints = "arch=amd64"
  model_uuid  = "00000000-0000-0000-0000-000000000001"
  resources   = { penpot-image = "1" }
  revision    = 10
  units       = 2
}

mock_provider "juju" {
  mock_resource "juju_application" {
    override_during = plan
    defaults = {
      id         = "penpot"
      model_type = "kubernetes"
    }
  }
}

run "deploys_application" {
  command = plan

  assert {
    condition     = output.application.name == "penpot" && output.application.model_uuid == "00000000-0000-0000-0000-000000000001" && output.application.charm[0].name == "penpot" && output.application.charm[0].channel == "latest/edge" && output.application.charm[0].base == "ubuntu@24.04" && output.application.charm[0].revision == 10
    error_message = "application identity did not match the expected deployment"
  }

  assert {
    condition     = output.application.constraints == "arch=amd64"
    error_message = "application constraints did not match expected"
  }

  assert {
    condition     = output.application.units == 2
    error_message = "application units did not match expected"
  }

  assert {
    condition = output.provides == {
      grafana_dashboard = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "grafana-dashboard"
      }
      metrics_endpoint = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "metrics-endpoint"
      }
    }
    error_message = "provides outputs did not match expected structure"
  }

  assert {
    condition = output.requires == {
      ingress = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "ingress"
      }
      logging = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "logging"
      }
      oauth = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "oauth"
      }
      postgresql = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "postgresql"
      }
      redis = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "redis"
      }
      s3 = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "s3"
      }
      smtp = {
        kind     = "endpoint"
        name     = "penpot"
        endpoint = "smtp"
      }
    }
    error_message = "requires outputs did not match expected structure"
  }
}
