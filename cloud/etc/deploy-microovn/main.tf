# microovn.tf
#
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

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

resource "juju_application" "openstack-network-agents" {
  name       = "openstack-network-agents"
  model_uuid = data.juju_model.machine_model.uuid

  charm {
    name     = "openstack-network-agents"
    channel  = var.charm_openstack_network_agents_channel
    base     = "ubuntu@26.04"
    revision = var.charm_openstack_network_agents_revision
  }

  config = merge({
    use-data-binding = true
  }, var.charm_openstack_network_agents_config)

  endpoint_bindings = var.openstack_network_agents_endpoint_bindings
}

resource "juju_application" "microcluster-token-distributor" {
  name       = "microcluster-token-distributor"
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.token_distributor_machine_ids) == 0 ? null : toset(var.token_distributor_machine_ids)
  units      = length(var.token_distributor_machine_ids) == 0 ? 1 : null

  charm {
    name     = "microcluster-token-distributor"
    channel  = var.charm_microcluster_token_distributor_channel
    base     = "ubuntu@26.04"
    revision = var.charm_microcluster_token_distributor_revision
  }

  config = var.charm_microcluster_token_distributor_config
}

resource "juju_application" "microovn" {
  name       = "microovn"
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(var.microovn_machine_ids) == 0 ? null : toset(var.microovn_machine_ids)
  units      = length(var.microovn_machine_ids) == 0 ? 1 : null

  charm {
    name     = "microovn"
    channel  = var.charm_microovn_channel
    base     = "ubuntu@26.04"
    revision = var.charm_microovn_revision
  }

  config = var.charm_microovn_config

  endpoint_bindings = var.endpoint_bindings
}

resource "juju_application" "sunbeam-ovn-proxy" {
  name       = "sunbeam-ovn-proxy"
  model_uuid = data.juju_model.machine_model.uuid
  # Only deploy when microovn is the SDN provider
  count = var.ovn-relay-offer-url == null ? 1 : 0
  # Deploy on same machine as token distributor
  machines = length(var.token_distributor_machine_ids) == 0 ? null : toset(var.token_distributor_machine_ids)
  units    = length(var.token_distributor_machine_ids) == 0 ? 1 : null

  charm {
    name     = "sunbeam-ovn-proxy"
    channel  = var.charm_sunbeam_ovn_proxy_channel
    base     = "ubuntu@26.04"
    revision = var.charm_sunbeam_ovn_proxy_revision
  }

  config = var.charm_sunbeam_ovn_proxy_config
}

resource "juju_integration" "microovn-microcluster-token-distributor" {
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "cluster"
  }

  application {
    name     = juju_application.microcluster-token-distributor.name
    endpoint = "microcluster-cluster"
  }
}

resource "juju_integration" "microovn-certs" {
  count      = (var.ca-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "certificates"
  }

  application {
    offer_url = var.ca-offer-url
  }
}

resource "juju_integration" "microovn-ovsdb-cms" {
  count      = (var.ovn-relay-offer-url != null) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "ovsdb-external"
  }

  application {
    offer_url = var.ovn-relay-offer-url
  }
}

resource "juju_integration" "microovn-openstack-network-agents" {
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.openstack-network-agents.name
    endpoint = "juju-info"
  }
}

resource "juju_integration" "microovn-to-ovn-proxy" {
  count      = length(juju_application.sunbeam-ovn-proxy.*.name) > 0 ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "ovsdb"
  }

  application {
    name     = juju_application.sunbeam-ovn-proxy[0].name
    endpoint = "ovsdb"
  }
}

resource "juju_offer" "ovsdb-cms" {
  count            = length(juju_application.sunbeam-ovn-proxy.*.name) > 0 ? 1 : 0
  model_uuid       = data.juju_model.machine_model.uuid
  application_name = juju_application.sunbeam-ovn-proxy[0].name
  endpoints        = ["ovsdb-cms"]
}

output "microovn-application-name" {
  value = juju_application.microovn.name
}

output "ovsdb-cms-offer" {
  value = try(juju_offer.ovsdb-cms[0].url, null)
}
