# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

run "basic_deploy" {
  variables {
    model_uuid = run.setup_tests.model_uuid
    base       = "ubuntu@24.04"
    channel    = "latest/edge"
    # renovate: depName="penpot"
    revision = 86
  }

  assert {
    condition     = output.application.name == "penpot"
    error_message = "penpot application name did not match expected"
  }

  assert {
    condition     = output.provides.grafana_dashboard.endpoint == "grafana-dashboard"
    error_message = "penpot module should provide 'provides.grafana_dashboard' output"
  }

  assert {
    condition     = output.provides.metrics_endpoint.endpoint == "metrics-endpoint"
    error_message = "penpot module should provide 'provides.metrics_endpoint' output"
  }

  assert {
    condition     = output.requires.ingress.endpoint == "ingress"
    error_message = "penpot module should provide 'requires.ingress' output"
  }

  assert {
    condition     = output.requires.logging.endpoint == "logging"
    error_message = "penpot module should provide 'requires.logging' output"
  }

  assert {
    condition     = output.requires.oauth.endpoint == "oauth"
    error_message = "penpot module should provide 'requires.oauth' output"
  }
}
