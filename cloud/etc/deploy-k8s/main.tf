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

data "juju_model" "machine_model" {
  name = var.machine_model
}

resource "juju_application" "k8s" {
  name     = "k8s"
  model    = data.juju_model.machine_model.name
  machines = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units    = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "k8s"
    channel  = var.k8s_channel
    revision = var.k8s_revision
    base     = "ubuntu@24.04"
  }

  config = var.k8s_config
  endpoint_bindings = var.endpoint_bindings
}
