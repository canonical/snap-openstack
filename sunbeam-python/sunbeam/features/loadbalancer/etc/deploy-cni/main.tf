# Terraform manifest for Loadbalancer Amphora infrastructure
# Deploys Multus CNI and OpenStack Port CNI charms for Octavia Amphora support
#
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

data "juju_model" "openstack" {
  uuid = var.model_uuid
}

resource "juju_application" "multus" {
  name  = "multus"
  trust = true
  model_uuid = data.juju_model.openstack.uuid

  charm {
    name     = "multus"
    channel  = var.multus-channel
    revision = var.multus-revision
  }

  config = merge(
    var.multus-config,
    var.multus-network-attachment-definitions != "" ? {
      "network-attachment-definitions" = var.multus-network-attachment-definitions
    } : {}
  )
}

resource "juju_application" "openstack-port-cni" {
  name  = "openstack-port-cni"
  trust = true
  model_uuid = data.juju_model.openstack.uuid

  charm {
    name     = "openstack-port-cni-k8s"
    channel  = var.openstack-port-cni-channel
    revision = var.openstack-port-cni-revision
  }

  config = var.openstack-port-cni-config
}

resource "juju_integration" "port-cni-keystone" {
  model_uuid = data.juju_model.openstack.uuid

  application {
    name     = juju_application.openstack-port-cni.name
    endpoint = "identity-credentials"
  }

  application {
    name     = "keystone"
    endpoint = "identity-credentials"
  }
}
