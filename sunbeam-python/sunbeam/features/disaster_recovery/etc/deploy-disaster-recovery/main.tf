# SPDX-FileCopyrightText: 2026 - Canonical Ltd
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

data "juju_model" "openstack_model" {
  uuid = var.openstack-model-uuid
}

resource "juju_application" "s3_integrator" {
  for_each   = var.enable-disaster-recovery ? toset(var.s3-integrator-apps) : []
  name       = each.value
  model_uuid = data.juju_model.openstack_model.uuid

  charm {
    name     = "s3-integrator"
    channel  = var.s3-integrator-channel
    revision = var.s3-integrator-revision
  }

  config = contains(keys(var.s3-integrator-secret-data), each.value) ? merge(
    lookup(var.s3-integrator-config, each.value, {}),
    { credentials = juju_secret.s3_credentials[each.value].secret_uri }
  ) : lookup(var.s3-integrator-config, each.value, {})
}

resource "juju_secret" "s3_credentials" {
  for_each   = var.enable-disaster-recovery ? var.s3-integrator-secret-data : {}
  model_uuid = data.juju_model.openstack_model.uuid
  name       = "${each.key}-credentials"
  value      = each.value
}

resource "juju_access_secret" "s3_credentials_access" {
  for_each     = var.enable-disaster-recovery ? var.s3-integrator-secret-data : {}
  model_uuid   = data.juju_model.openstack_model.uuid
  secret_id    = juju_secret.s3_credentials[each.key].secret_id
  applications = [each.key]

  depends_on = [juju_application.s3_integrator]
}

resource "juju_integration" "s3_integrations" {
  for_each   = var.enable-disaster-recovery ? var.s3-integrations : {}
  model_uuid = data.juju_model.openstack_model.uuid

  application {
    name     = each.value.integrator_app
    endpoint = "s3-credentials"
  }

  application {
    name     = each.key
    endpoint = each.value.target_endpoint
  }

  depends_on = [juju_access_secret.s3_credentials_access]
}
