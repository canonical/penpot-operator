# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "metadata" {
  description = "Metadata of the product module deployment."
  value = {
    version     = var.metadata_version
    risk        = var.risk
    deployed_at = terraform_data.deployed_at.output
    updated_at  = timestamp()
  }
}

output "models" {
  description = "Map of the model key to its model UUID and the components deployed in it."
  value = merge(
    {
      penpot = {
        model_uuid = var.model_uuid
        components = merge(
          { penpot = module.penpot.application },
          { for app in juju_application.redis_k8s : "redis_k8s" => app },
          { for app in juju_application.s3_integrator : "s3_integrator" => app },
        )
      }
    },
    {
      for app in juju_application.postgresql : "database" => {
        model_uuid = var.database_model_uuid
        components = { postgresql = app }
      }
    },
  )
}

output "offers" {
  description = "Map of the offers created by the product module with their URLs."
  value       = { for offer in juju_offer.postgresql : "postgresql" => offer.url }
}
