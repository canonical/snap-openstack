# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
    }
  }

}

data "juju_model" "machine_model" {
  name = var.machine_model
}

resource "juju_application" "cinder-volume" {
  name     = var.application_name
  model    = data.juju_model.machine_model.name
  machines = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units    = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "cinder-volume"
    channel  = var.charm_channel
    revision = var.charm_revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    snap-channel                   = var.cinder_volume_channel
    enable-telemetry-notifications = var.enable-telemetry-notifications
  }, var.charm_config)
  endpoint_bindings = var.endpoint_bindings
}

resource "juju_offer" "storage-backend-offer" {
  application_name = juju_application.cinder-volume.name
  endpoints        = ["storage-backend"]
  model            = data.juju_model.machine_model.name
}

resource "juju_integration" "cinder-volume-identity" {
  count = (var.keystone-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = var.keystone-offer-url
    endpoint  = "identity-credentials"
  }
}

resource "juju_integration" "cinder-volume-amqp" {
  count = (var.amqp-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "amqp"
  }

  application {
    offer_url = var.amqp-offer-url
  }
}

resource "juju_integration" "cinder-volume-database" {
  count = (var.database-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "database"
  }

  application {
    offer_url = var.database-offer-url
  }
}
