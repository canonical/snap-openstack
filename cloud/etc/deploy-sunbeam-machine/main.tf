# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {

  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.20.0"
    }
  }

}

provider "juju" {}

resource "juju_application" "sunbeam-machine" {
  name  = "sunbeam-machine"
  trust = false
  model = var.machine_model
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name     = "sunbeam-machine"
    channel  = var.charm_channel
    revision = var.charm_revision
    base    = "ubuntu@24.04"
  }

  config = var.charm_config
  endpoint_bindings = var.endpoint_bindings

}
