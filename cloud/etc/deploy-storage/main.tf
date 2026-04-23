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

data "juju_model" "model" {
  uuid = var.model
}

module "backends" {
  for_each = var.backends

  source = "./modules/backend"

  model_uuid = data.juju_model.model.uuid

  application_name      = each.value.application_name
  units                 = each.value.units
  name                  = each.key
  principal_application = each.value.principal_application
  charm_name            = each.value.charm_name
  charm_base            = each.value.charm_base
  charm_channel         = each.value.charm_channel
  charm_revision        = each.value.charm_revision
  charm_config          = each.value.charm_config
  endpoint_bindings     = each.value.endpoint_bindings
  secrets               = each.value.secrets
  extra_integrations    = each.value.extra_integrations
}

module "cinder-volume" {
  for_each = var.cinder-volumes
  source   = "./modules/cinder-volume"

  machine_model_uuid             = data.juju_model.model.uuid
  application_name               = each.value.application_name
  charm_channel                  = each.value.charm_channel
  charm_revision                 = each.value.charm_revision
  charm_config                   = each.value.charm_config
  machine_ids                    = each.value.machine_ids
  endpoint_bindings              = each.value.endpoint_bindings
  keystone-offer-url             = each.value.keystone-offer-url
  amqp-offer-url                 = each.value.amqp-offer-url
  database-offer-url             = each.value.database-offer-url
  cert-distributor-offer-url     = each.value.cert-distributor-offer-url
  enable-telemetry-notifications = each.value.enable-telemetry-notifications
}
