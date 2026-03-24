# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 1.3.1"
    }
  }
}

provider "juju" {}

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

resource "juju_application" "sunbeam-machine" {
  name       = "sunbeam-machine"
  trust      = false
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units      = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "sunbeam-machine"
    channel  = var.charm_channel
    revision = var.charm_revision
    base     = "ubuntu@24.04"
  }

  config            = var.charm_config
  endpoint_bindings = var.endpoint_bindings
}

resource "juju_application" "epa_orchestrator" {
  name       = "epa-orchestrator"
  model_uuid = data.juju_model.machine_model.uuid

  charm {
    name     = "epa-orchestrator"
    channel  = var.charm_epa_orchestrator_channel
    revision = var.charm_epa_orchestrator_revision
    base     = "ubuntu@24.04"
  }

  config            = var.charm_epa_orchestrator_config
  endpoint_bindings = var.epa_orchestrator_endpoint_bindings
}

resource "juju_integration" "epa-orchestrator-to-sunbeam-machine" {
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.epa_orchestrator.name
    endpoint = "sunbeam-machine"
  }

  application {
    name     = juju_application.sunbeam-machine.name
    endpoint = "sunbeam-machine"
  }
}
