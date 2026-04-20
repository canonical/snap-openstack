# SPDX-FileCopyrightText: 2025 - Canonical Ltd
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

resource "juju_application" "manila-data" {
  name       = "manila-data"
  trust      = true
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units      = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "manila-data"
    channel  = var.charm-manila-data-channel
    revision = var.charm-manila-data-revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    snap-channel = var.manila-data-channel
  }, var.charm-manila-data-config)
  endpoint_bindings = var.endpoint_bindings
}

resource "juju_integration" "manila-data-identity" {
  count      = (var.keystone-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.manila-data.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = var.keystone-offer-url
    endpoint  = "identity-credentials"
  }
}

resource "juju_integration" "manila-data-amqp" {
  count      = (var.amqp-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.manila-data.name
    endpoint = "amqp"
  }

  application {
    offer_url = var.amqp-offer-url
  }
}

resource "juju_integration" "manila-data-database" {
  count      = (var.database-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.manila-data.name
    endpoint = "database"
  }

  application {
    offer_url = var.database-offer-url
  }
}
