# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {

  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.20.0"
    }
  }

}

provider "juju" {}

resource "juju_application" "openstack-hypervisor" {
  name     = "openstack-hypervisor"
  trust    = false
  model    = var.machine_model
  machines = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units    = length(var.machine_ids) == 0 ? 0 : null

  charm {
    name     = "openstack-hypervisor"
    channel  = var.charm_channel
    revision = var.charm_revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    snap-channel          = var.snap_channel
    use-migration-binding = true
    use-data-binding      = true
  }, var.charm_config)
  endpoint_bindings = var.endpoint_bindings
}

resource "juju_integration" "hypervisor-amqp" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "amqp"
  }

  application {
    offer_url = var.rabbitmq-offer-url
  }
}

resource "juju_integration" "hypervisor-identity" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = var.keystone-offer-url
    endpoint  = "identity-credentials"
  }
}

resource "juju_integration" "hypervisor-cert-distributor" {
  count = (var.cert-distributor-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "receive-ca-cert"
  }

  application {
    offer_url = var.cert-distributor-offer-url
    endpoint  = "send-ca-cert"
  }
}

resource "juju_integration" "hypervisor-certs" {
  count = (var.ca-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "certificates"
  }

  application {
    offer_url = var.ca-offer-url
  }
}

resource "juju_integration" "hypervisor-ovn" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ovsdb-cms"
  }

  application {
    offer_url = var.ovn-relay-offer-url
  }
}

resource "juju_integration" "hypervisor-ceilometer" {
  count = (var.ceilometer-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ceilometer-service"
  }

  application {
    offer_url = var.ceilometer-offer-url
  }
}

resource "juju_integration" "hypervisor-cinder-ceph" {
  count = (var.cinder-volume-ceph-application-name != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ceph-access"
  }

  application {
    name     = var.cinder-volume-ceph-application-name
    endpoint = "ceph-access"
  }
}

resource "juju_integration" "hypervisor-nova-controller" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "nova-service"
  }

  application {
    offer_url = var.nova-offer-url
  }
}

resource "juju_integration" "hypervisor-masakari" {
  count = (var.masakari-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "masakari-service"
  }

  application {
    offer_url = var.masakari-offer-url
  }
}
