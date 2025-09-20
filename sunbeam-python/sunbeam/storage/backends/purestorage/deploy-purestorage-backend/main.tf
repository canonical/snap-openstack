# SPDX-FileCopyrightText: 2025 - Canonical Ltd
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

# Deploy Pure Storage backend charms
resource "juju_application" "purestorage_backends" {
  for_each = var.purestorage_backends

  name  = each.key
  model = data.juju_model.machine_model.name
  units = 1

  charm {
    name     = var.charm_purestorage_name
    channel  = var.charm_purestorage_channel
    revision = var.charm_purestorage_revision
    base     = var.charm_purestorage_base
  }

  config = merge({
    volume-backend-name = each.key
  }, each.value.charm_config)

  endpoint_bindings = var.endpoint_bindings
}

# Integrate Pure Storage backends with main cinder-volume
resource "juju_integration" "purestorage_to_cinder_volume" {
  for_each = var.purestorage_backends

  model = var.machine_model

  application {
    name     = juju_application.purestorage_backends[each.key].name
    endpoint = var.charm_purestorage_endpoint
  }

  application {
    name     = "cinder-volume"
    endpoint = var.charm_purestorage_endpoint
  }
}
