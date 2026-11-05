# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

run "missing_database_model" {
  command = plan

  variables {
    model_uuid          = run.setup_tests.model_uuid
    database_model_uuid = null
  }

  expect_failures = [var.database_model_uuid]
}

run "external_integrations" {
  command = plan

  variables {
    model_uuid = run.setup_tests.model_uuid
    postgresql_integration = {
      offer_url = "admin/database.postgresql"
    }
    valkey_integration = {
      name = "valkey"
    }
    s3_integration = {
      offer_url = "admin/storage.s3"
    }
    ingress_integration = {
      name     = "traefik-k8s"
      endpoint = "ingress"
    }
  }

  assert {
    condition     = length(juju_application.postgresql) == 0 && length(juju_offer.postgresql) == 0
    error_message = "the bundled postgresql should not be deployed when postgresql_integration is set"
  }

  assert {
    condition     = length(juju_application.valkey) == 0 && length(juju_application.s3_integrator) == 0
    error_message = "the bundled valkey and s3-integrator should not be deployed when their integrations are set"
  }

  assert {
    condition     = keys(juju_integration.penpot_optional) == ["ingress"]
    error_message = "only the ingress optional integration should be created"
  }
}

run "basic_deploy" {
  variables {
    model_uuid          = run.setup_tests.model_uuid
    database_model_uuid = run.setup_tests.database_model_uuid
    risk                = "edge"
    penpot = {
      # renovate: depName="penpot"
      revision = 87
    }
    postgresql = {
      # renovate: depName="postgresql"
      revision = 1236
    }
    s3_integrator = {
      # renovate: depName="s3-integrator"
      revision = 637
    }
    valkey = {
      # renovate: depName="valkey"
      revision = 156
    }
  }

  assert {
    condition     = output.models.penpot.model_uuid == run.setup_tests.model_uuid
    error_message = "the penpot model UUID did not match the provided model_uuid"
  }

  assert {
    condition     = keys(output.models.penpot.components) == ["penpot", "s3_integrator", "valkey"]
    error_message = "the penpot model should contain penpot, s3-integrator and valkey"
  }

  assert {
    condition     = output.models.database.model_uuid == run.setup_tests.database_model_uuid
    error_message = "the database model UUID did not match the provided database_model_uuid"
  }

  assert {
    condition     = output.models.database.components.postgresql.name == "postgresql"
    error_message = "the database model should contain postgresql"
  }

  assert {
    condition     = output.offers.postgresql != ""
    error_message = "the postgresql offer URL should be exposed"
  }

  assert {
    condition     = output.metadata.risk == "edge"
    error_message = "the metadata should report the requested risk"
  }
}
