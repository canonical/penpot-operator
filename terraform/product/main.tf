# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# Captures the initial deployment timestamp once and keeps it stable across
# subsequent applies, for the "metadata.deployed_at" output.
resource "terraform_data" "deployed_at" {
  input = timestamp()

  lifecycle {
    ignore_changes = [input]
  }
}

module "penpot" {
  source = "../charm"

  app_name    = var.penpot.app_name
  base        = var.penpot.base
  channel     = local.penpot_channel
  config      = var.penpot.config
  constraints = var.penpot.constraints
  model_uuid  = var.model_uuid
  resources   = var.penpot.resources
  revision    = var.penpot.revision
  units       = var.penpot.units
}

resource "juju_application" "postgresql" {
  count = local.deploy_postgresql ? 1 : 0

  name       = var.postgresql.app_name
  model_uuid = var.database_model_uuid

  charm {
    name     = "postgresql"
    channel  = local.postgresql_channel
    revision = var.postgresql.revision
    base     = var.postgresql.base
  }

  config      = var.postgresql.config
  constraints = var.postgresql.constraints
  units       = var.postgresql.units
}

resource "juju_offer" "postgresql" {
  count = local.deploy_postgresql ? 1 : 0

  model_uuid       = var.database_model_uuid
  application_name = juju_application.postgresql[0].name
  endpoints        = ["database"]
}

resource "juju_application" "redis_k8s" {
  count = local.deploy_redis_k8s ? 1 : 0

  name       = var.redis_k8s.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "redis-k8s"
    channel  = local.redis_k8s_channel
    revision = var.redis_k8s.revision
    base     = var.redis_k8s.base
  }

  config      = var.redis_k8s.config
  constraints = var.redis_k8s.constraints
  resources   = var.redis_k8s.resources
  units       = var.redis_k8s.units
}

resource "juju_application" "s3_integrator" {
  count = local.deploy_s3_integrator ? 1 : 0

  name       = var.s3_integrator.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "s3-integrator"
    channel  = local.s3_integrator_channel
    revision = var.s3_integrator.revision
    base     = var.s3_integrator.base
  }

  config      = var.s3_integrator.config
  constraints = var.s3_integrator.constraints
  units       = var.s3_integrator.units
}

resource "juju_integration" "penpot_postgresql" {
  model_uuid = var.model_uuid

  application {
    name     = module.penpot.application.name
    endpoint = module.penpot.requires.postgresql.endpoint
  }

  application {
    name      = local.postgresql_target.name
    offer_url = local.postgresql_target.offer_url
    endpoint  = local.postgresql_target.endpoint
  }
}

resource "juju_integration" "penpot_redis" {
  model_uuid = var.model_uuid

  application {
    name     = module.penpot.application.name
    endpoint = module.penpot.requires.redis.endpoint
  }

  application {
    name      = local.redis_target.name
    offer_url = local.redis_target.offer_url
    endpoint  = local.redis_target.endpoint
  }
}

resource "juju_integration" "penpot_s3" {
  model_uuid = var.model_uuid

  application {
    name     = module.penpot.application.name
    endpoint = module.penpot.requires.s3.endpoint
  }

  application {
    name      = local.s3_target.name
    offer_url = local.s3_target.offer_url
    endpoint  = local.s3_target.endpoint
  }
}

resource "juju_integration" "penpot_optional" {
  for_each = local.optional_integrations

  model_uuid = var.model_uuid

  application {
    name     = module.penpot.application.name
    endpoint = module.penpot.requires[each.key].endpoint
  }

  application {
    name      = each.value.name
    offer_url = each.value.offer_url
    endpoint  = each.value.endpoint
  }
}
