# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source = "juju/juju"
    }
  }
}

data "juju_model" "model" {
  name = var.model
}

data "juju_application" "cinder-volume" {
  name  = var.principal_application
  model = data.juju_model.model.name
}

resource "juju_secret" "secret" {
  for_each = var.secrets

  model = data.juju_model.model.name
  name  = "${var.name}-${each.key}-config-secret"
  value = each.value
}

resource "juju_access_secret" "secret-access" {
  for_each = juju_secret.secret

  model        = each.value.model
  secret_id    = each.value.secret_id
  applications = [juju_application.storage-backend.name]
}

locals {
  charm_config = merge(
    { volume-backend-name = var.name },
    var.charm_config,
    # Only template secrets uris in charm config if they have a value
    { for k, v in juju_secret.secret : k => v.secret_uri }
  )
}

# Deploy Storage backend charms
resource "juju_application" "storage-backend" {
  name  = var.name
  model = data.juju_model.model.uuid
  units = 1

  charm {
    name     = var.charm_name
    channel  = var.charm_channel
    revision = var.charm_revision
    base     = var.charm_base
  }

  config = local.charm_config

  endpoint_bindings = var.endpoint_bindings
}

# Integrate Storage backends with cinder-volume
resource "juju_integration" "storage-backend-to-cinder-volume" {
  model = data.juju_model.model.name

  application {
    name     = juju_application.storage-backend.name
    endpoint = "cinder-volume"
  }

  application {
    name     = data.juju_application.cinder-volume.name
    endpoint = "cinder-volume"
  }
}
