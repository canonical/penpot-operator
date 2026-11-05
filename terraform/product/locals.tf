# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # The product owns each charm's track; `risk` selects the channel risk solution-wide.
  penpot_channel        = coalesce(var.penpot.channel, "latest/${var.risk}")
  postgresql_channel    = coalesce(var.postgresql.channel, "16/${var.risk}")
  s3_integrator_channel = coalesce(var.s3_integrator.channel, "1/${var.risk}")
  valkey_channel        = coalesce(var.valkey.channel, "9/${var.risk}")

  deploy_postgresql    = var.postgresql_integration == null
  deploy_s3_integrator = var.s3_integration == null
  deploy_valkey        = var.valkey_integration == null

  # The bundled postgresql lives in the machine model, so penpot consumes it through an offer.
  postgresql_target = local.deploy_postgresql ? {
    name      = null
    offer_url = one(juju_offer.postgresql[*].url)
    endpoint  = null
  } : var.postgresql_integration
  s3_target = local.deploy_s3_integrator ? {
    name      = one(juju_application.s3_integrator[*].name)
    offer_url = null
    endpoint  = "s3-credentials"
  } : var.s3_integration
  valkey_target = local.deploy_valkey ? {
    name      = one(juju_application.valkey[*].name)
    offer_url = null
    endpoint  = "valkey-client"
  } : var.valkey_integration

  optional_integrations = {
    for key, target in {
      ingress = var.ingress_integration
      smtp    = var.smtp_integration
      logging = var.logging_integration
    } : key => target if target != null
  }
}
