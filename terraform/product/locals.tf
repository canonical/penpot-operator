# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # The product owns each charm's track; `risk` selects the channel risk solution-wide.
  penpot_channel        = coalesce(var.penpot.channel, "latest/${var.risk}")
  postgresql_channel    = coalesce(var.postgresql.channel, "16/${var.risk}")
  redis_k8s_channel     = coalesce(var.redis_k8s.channel, "latest/${var.risk}")
  s3_integrator_channel = coalesce(var.s3_integrator.channel, "1/${var.risk}")

  deploy_postgresql    = var.postgresql_integration == null
  deploy_redis_k8s     = var.redis_integration == null
  deploy_s3_integrator = var.s3_integration == null

  # The bundled postgresql lives in the machine model, so penpot consumes it through an offer.
  postgresql_target = local.deploy_postgresql ? {
    name      = null
    offer_url = one(juju_offer.postgresql[*].url)
    endpoint  = null
  } : var.postgresql_integration
  redis_target = local.deploy_redis_k8s ? {
    name      = one(juju_application.redis_k8s[*].name)
    offer_url = null
    endpoint  = "redis"
  } : var.redis_integration
  s3_target = local.deploy_s3_integrator ? {
    name      = one(juju_application.s3_integrator[*].name)
    offer_url = null
    endpoint  = "s3-credentials"
  } : var.s3_integration

  optional_integrations = {
    for key, target in {
      ingress = var.ingress_integration
      smtp    = var.smtp_integration
      logging = var.logging_integration
    } : key => target if target != null
  }
}
