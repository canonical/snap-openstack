# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.23.1"
    }
  }
}

provider "juju" {}

data "juju_model" "model" {
  name = var.model
}

module "backends" {
  for_each = var.backends

  source = "./modules/backend"

  model = data.juju_model.model.uuid

  name                  = each.key
  principal_application = each.value.principal_application
  charm_name            = each.value.charm_name
  charm_base            = each.value.charm_base
  charm_channel         = each.value.charm_channel
  charm_revision        = each.value.charm_revision
  charm_config          = each.value.charm_config
  endpoint_bindings     = each.value.endpoint_bindings
  secrets               = each.value.secrets
}
