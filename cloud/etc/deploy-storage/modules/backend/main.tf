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
  uuid = var.model_uuid
}

data "juju_application" "cinder-volume" {
  name       = var.principal_application
  model_uuid = data.juju_model.model.uuid
}

resource "juju_secret" "secret" {
  model_uuid = data.juju_model.model.uuid
  name       = "${var.name}-config-secret"
  value = {
    # Only template secrets that have a corresponding charm config value
    for k, v in var.secrets : v => var.charm_config[k] if can(var.charm_config[k])
  }
}

resource "juju_access_secret" "secret-access" {
  model_uuid   = data.juju_model.model.uuid
  secret_id    = juju_secret.secret.secret_id
  applications = [juju_application.storage-backend.name]
}

locals {
  charm_config = merge(
    { volume-backend-name = var.name },
    var.charm_config,
    # Only template secrets uris in charm config if they have a value
    { for k, v in var.secrets : k => juju_secret.secret.secret_uri if can(var.charm_config[k]) }
  )
}

# Deploy Storage backend charms
resource "juju_application" "storage-backend" {
  name       = var.name
  model_uuid = data.juju_model.model.uuid
  units      = 1

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
  model_uuid = data.juju_model.model.uuid

  application {
    name     = juju_application.storage-backend.name
    endpoint = "cinder-volume"
  }

  application {
    name     = data.juju_application.cinder-volume.name
    endpoint = "cinder-volume"
  }
}
