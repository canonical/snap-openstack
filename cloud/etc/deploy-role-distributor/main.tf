# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 1.5.6"
    }
  }
}

provider "juju" {}

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

resource "juju_application" "role-distributor" {
  name       = "role-distributor"
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.role_distributor_machine_ids) == 0 ? null : toset(var.role_distributor_machine_ids)
  units      = length(var.role_distributor_machine_ids) == 0 ? 1 : null

  charm {
    name     = "role-distributor"
    channel  = var.charm_role_distributor_channel
    base     = "ubuntu@24.04"
    revision = var.charm_role_distributor_revision
  }

  config = var.charm_role_distributor_config
}

output "role-distributor-application-name" {
  value = juju_application.role-distributor.name
}
