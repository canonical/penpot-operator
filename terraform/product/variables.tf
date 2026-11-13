# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variable "database_model_uuid" {
  description = "UUID of the machine Juju model to deploy the bundled postgresql into. Required unless `postgresql_integration` is set."
  type        = string
  default     = null

  validation {
    condition     = var.database_model_uuid != null || var.postgresql_integration != null
    error_message = "database_model_uuid must be set when postgresql_integration is not provided."
  }
}

variable "ingress_integration" {
  description = "`ingress` provider (for example traefik-k8s) to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. Leave null to skip the integration."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.ingress_integration == null || ((try(var.ingress_integration.name, null) == null) != (try(var.ingress_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in ingress_integration."
  }
}

variable "logging_integration" {
  description = "`loki_push_api` provider to send penpot logs to, given as an application `name` in `model_uuid` or an `offer_url`. Leave null to skip the integration."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.logging_integration == null || ((try(var.logging_integration.name, null) == null) != (try(var.logging_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in logging_integration."
  }
}

variable "metadata_version" {
  description = "Version string reported in the product module `metadata` output."
  type        = string
  default     = "1.0.0"
}

variable "model_uuid" {
  description = "UUID of the Kubernetes Juju model to deploy penpot, redis-k8s and s3-integrator into."
  type        = string
  nullable    = false
}

variable "penpot" {
  description = "Configuration of the penpot charm. When `channel` is null it defaults to `latest/<risk>`."
  type = object({
    app_name    = optional(string, "penpot")
    base        = optional(string, "ubuntu@24.04")
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    resources   = optional(map(string), {})
    revision    = optional(number)
    units       = optional(number, 1)
  })
  default = {}
}

variable "postgresql" {
  description = "Configuration of the bundled postgresql machine charm, deployed in `database_model_uuid` unless `postgresql_integration` is set. When `channel` is null it defaults to `16/<risk>`."
  type = object({
    app_name    = optional(string, "postgresql")
    base        = optional(string, "ubuntu@24.04")
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    revision    = optional(number)
    units       = optional(number, 1)
  })
  default = {}
}

variable "postgresql_integration" {
  description = "Existing `postgresql_client` provider to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. When set, the bundled postgresql is not deployed."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.postgresql_integration == null || ((try(var.postgresql_integration.name, null) == null) != (try(var.postgresql_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in postgresql_integration."
  }
}

variable "redis_integration" {
  description = "Existing `redis` provider to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. When set, the bundled redis-k8s is not deployed."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.redis_integration == null || ((try(var.redis_integration.name, null) == null) != (try(var.redis_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in redis_integration."
  }
}

variable "redis_k8s" {
  description = "Configuration of the bundled redis-k8s charm, deployed unless `redis_integration` is set. When `channel` is null it defaults to `latest/<risk>`."
  type = object({
    app_name    = optional(string, "redis-k8s")
    base        = optional(string, "ubuntu@22.04")
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    resources   = optional(map(string), {})
    revision    = optional(number)
    units       = optional(number, 1)
  })
  default = {}
}

variable "risk" {
  description = "Risk level controlling the channel risk of the charms in the solution. Every charm `channel` left unset defaults to `<track>/<risk>`."
  type        = string
  default     = "stable"

  validation {
    condition     = contains(["stable", "edge"], var.risk)
    error_message = "risk must be one of: stable, edge."
  }
}

variable "s3_integration" {
  description = "Existing `s3` provider to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. When set, the bundled s3-integrator is not deployed."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.s3_integration == null || ((try(var.s3_integration.name, null) == null) != (try(var.s3_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in s3_integration."
  }
}

variable "s3_integrator" {
  description = "Configuration of the bundled s3-integrator charm, deployed unless `s3_integration` is set. When `channel` is null it defaults to `1/<risk>`. Set the S3 credentials with the `sync-s3-credentials` action after deployment."
  type = object({
    app_name    = optional(string, "s3-integrator")
    base        = optional(string, "ubuntu@22.04")
    channel     = optional(string)
    config      = optional(map(string), {})
    constraints = optional(string)
    revision    = optional(number)
    units       = optional(number, 1)
  })
  default = {}
}

variable "smtp_integration" {
  description = "`smtp` provider (for example smtp-integrator) to integrate penpot with, given as an application `name` in `model_uuid` or an `offer_url`. Leave null to skip the integration."
  type = object({
    name      = optional(string)
    offer_url = optional(string)
    endpoint  = optional(string)
  })
  default = null

  validation {
    condition     = var.smtp_integration == null || ((try(var.smtp_integration.name, null) == null) != (try(var.smtp_integration.offer_url, null) == null))
    error_message = "Set exactly one of name or offer_url in smtp_integration."
  }
}
