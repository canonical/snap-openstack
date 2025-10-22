# SPDX-FileCopyrightText: 2023 - Canonical Ltd
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

data "juju_model" "machine_model" {
  name = var.machine_model
}

resource "juju_application" "microceph" {
  name     = "microceph"
  trust    = true
  model    = data.juju_model.machine_model.name
  machines = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units    = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "microceph"
    channel  = var.charm_microceph_channel
    revision = var.charm_microceph_revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    snap-channel = var.microceph_channel
  }, var.charm_microceph_config)
  endpoint_bindings = var.endpoint_bindings
}

# juju_offer.microceph_offer will be created
resource "juju_offer" "microceph_offer" {
  name             = "microceph"
  application_name = juju_application.microceph.name
  endpoints        = ["ceph"]
  model            = data.juju_model.machine_model.name
}

# juju_offer.microceph_ceph_nfs_offer will be created
resource "juju_offer" "microceph_ceph_nfs_offer" {
  name             = "microceph-ceph-nfs"
  application_name = juju_application.microceph.name
  endpoints        = ["ceph-nfs"]
  model            = data.juju_model.machine_model.name
}

# juju_offer.microceph_ceph_rgw_offer will be created
resource "juju_offer" "microceph_ceph_rgw_offer" {
  name             = "microceph-ceph-rgw"
  application_name = juju_application.microceph.name
  endpoints        = ["ceph-rgw"]
  model            = data.juju_model.machine_model.name
}

resource "juju_integration" "microceph-identity" {
  count = (var.keystone-endpoints-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.microceph.name
    endpoint = "identity-service"
  }

  application {
    offer_url = var.keystone-endpoints-offer-url
    endpoint  = "identity-service"
  }
}

resource "juju_integration" "microceph-traefik-rgw" {
  count = (var.ingress-rgw-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.microceph.name
    endpoint = "traefik-route-rgw"
  }

  application {
    offer_url = var.ingress-rgw-offer-url
  }
}

resource "juju_integration" "microceph-cert-distributor" {
  count = (var.cert-distributor-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.microceph.name
    endpoint = "receive-ca-cert"
  }

  application {
    offer_url = var.cert-distributor-offer-url
  }
}

output "ceph-application-name" {
  value = juju_application.microceph.name
}
