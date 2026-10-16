# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "penpot" {
  name       = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "penpot"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config      = var.config
  constraints = var.constraints
  resources   = var.resources
  units       = var.units
}
