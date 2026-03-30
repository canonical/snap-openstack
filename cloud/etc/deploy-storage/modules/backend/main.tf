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
  count = length(local.secret_values) > 0 ? 1 : 0
  model = data.juju_model.model.name
  name  = "${var.name}-config-secret"
  value = local.secret_values
}

resource "juju_access_secret" "secret-access" {
  count        = length(local.secret_values) > 0 ? 1 : 0
  model        = juju_secret.secret[0].model
  secret_id    = juju_secret.secret[0].secret_id
  applications = [juju_application.storage-backend.name]
}

locals {
  application_name = var.application_name != null ? var.application_name : var.name

  secret_values = {
    # Only template secrets that have a corresponding charm config value
    for k, v in var.secrets : v => var.charm_config[k] if can(var.charm_config[k])
  }

  charm_config = merge(
    { volume-backend-name = var.name },
    var.charm_config,
    # Only template secrets uris in charm config if they have a value
    {
      for k, v in var.secrets :
      k => juju_secret.secret[0].secret_uri
      if length(local.secret_values) > 0 && can(var.charm_config[k])
    }
  )
}

# Deploy Storage backend charms
resource "juju_application" "storage-backend" {
  name  = local.application_name
  model = data.juju_model.model.uuid
  units = var.units

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

resource "juju_integration" "backend-extra-integration" {
  for_each = {
    for i in var.extra_integrations : "${i.application_name}-${i.endpoint_name}" => i
  }
  model = data.juju_model.model.name

  application {
    name     = juju_application.storage-backend.name
    endpoint = each.value.backend_endpoint_name
  }

  application {
    name     = each.value.application_name
    endpoint = each.value.endpoint_name
  }
}
