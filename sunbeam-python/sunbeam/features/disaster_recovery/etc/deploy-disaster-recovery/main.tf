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

  config = var.s3-integrator-config
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
}
