# SPDX-FileCopyrightText: 2023 - Canonical Ltd
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

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

resource "juju_application" "openstack-hypervisor" {
  name       = "openstack-hypervisor"
  trust      = false
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.machine_ids) == 0 ? null : toset(var.machine_ids)
  units      = length(var.machine_ids) == 0 ? 0 : null

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
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "amqp"
  }

  application {
    offer_url = var.rabbitmq-offer-url
  }
}

resource "juju_integration" "hypervisor-identity" {
  # TODO: make the keystone offer mandatory once the Terraform
  # Juju provider supports cross-controller relations.
  count      = can(coalesce(var.keystone-offer-url)) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

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
  count      = (var.cert-distributor-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

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
  count      = (var.ca-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "certificates"
  }

  application {
    offer_url = var.ca-offer-url
  }
}

moved {
  from = juju_integration.hypervisor-ovn
  to   = juju_integration.hypervisor-ovn[0]
}

moved {
  from = juju_integration.hypervisor-cinder-ceph[0]
  to   = juju_integration.hypervisor-extra-integration["cinder-volume-ceph-ceph-access"]
}

resource "juju_integration" "hypervisor-ovn" {
  # Should be deployed if ovn-relay-offer-url set
  count      = (var.ovn-relay-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ovsdb-cms"
  }

  application {
    offer_url = var.ovn-relay-offer-url
  }
}

resource "juju_integration" "hypervisor-ovn-proxy" {
  # Shouldn't be deployed if ovn-relay-offer-url is set
  count      = (var.ovn-relay-offer-url == null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid
  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ovsdb-cms"
  }

  application {
    name     = "sunbeam-ovn-proxy"
    endpoint = "ovsdb-cms"
  }
}

resource "juju_integration" "hypervisor-ceilometer" {
  count      = (var.ceilometer-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ceilometer-service"
  }

  application {
    offer_url = var.ceilometer-offer-url
  }
}

resource "juju_integration" "hypervisor-extra-integration" {
  for_each = {
    for i in var.extra_integrations : "${i.application_name}-${i.endpoint_name}" => i
  }
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = each.value.hypervisor_endpoint_name
  }

  application {
    name     = each.value.application_name
    endpoint = each.value.endpoint_name
  }
}

resource "juju_integration" "hypervisor-nova-controller" {
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "nova-service"
  }

  application {
    offer_url = var.nova-offer-url
  }
}

resource "juju_integration" "hypervisor-masakari" {
  count      = (var.masakari-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "masakari-service"
  }

  application {
    offer_url = var.masakari-offer-url
  }
}

resource "juju_integration" "hypervisor-barbican" {
  count      = (var.barbican-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "barbican-service"
  }

  application {
    offer_url = var.barbican-offer-url
  }
}
