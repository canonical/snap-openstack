# microovn.tf
#
# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 1.5.6"
    }
  }
}

provider "juju" {}

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

locals {
  microovn_machine_ids_by_architecture = merge(
    {
      amd64 = var.microovn_machine_ids
      arm64 = var.microovn_arm64_machine_ids
    },
    var.microovn_machine_ids_by_architecture,
  )
  microovn_machine_ids       = lookup(local.microovn_machine_ids_by_architecture, "amd64", [])
  microovn_arm64_machine_ids = lookup(local.microovn_machine_ids_by_architecture, "arm64", [])
}

resource "juju_application" "openstack-network-agents" {
  name       = "openstack-network-agents"
  model_uuid = data.juju_model.machine_model.uuid

  charm {
    name     = "openstack-network-agents"
    channel  = var.charm_openstack_network_agents_channel
    base     = "ubuntu@24.04"
    revision = var.charm_openstack_network_agents_revision
  }

  config = merge({
    use-data-binding = true
  }, var.charm_openstack_network_agents_config)

  endpoint_bindings = var.openstack_network_agents_endpoint_bindings
}

resource "juju_application" "openstack-network-agents-arm64" {
  count      = length(local.microovn_arm64_machine_ids) > 0 ? 1 : 0
  name       = "openstack-network-agents-arm64"
  model_uuid = data.juju_model.machine_model.uuid

  charm {
    name     = "openstack-network-agents"
    channel  = var.charm_openstack_network_agents_channel
    base     = "ubuntu@24.04"
    revision = var.charm_openstack_network_agents_arm64_revision
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
    base     = "ubuntu@24.04"
    revision = var.charm_microcluster_token_distributor_revision
  }

  config = var.charm_microcluster_token_distributor_config
}

resource "juju_application" "microovn" {
  name       = "microovn"
  model_uuid = data.juju_model.machine_model.uuid
  machines   = length(local.microovn_machine_ids) == 0 ? null : toset(local.microovn_machine_ids)
  units      = length(local.microovn_machine_ids) == 0 ? 0 : null

  charm {
    name     = "microovn"
    channel  = var.charm_microovn_channel
    base     = "ubuntu@24.04"
    revision = var.charm_microovn_revision
  }

  config = var.charm_microovn_config

  endpoint_bindings = var.endpoint_bindings
}

resource "juju_application" "microovn_arm64" {
  count       = length(local.microovn_arm64_machine_ids) > 0 ? 1 : 0
  name        = "microovn-arm64"
  model_uuid  = data.juju_model.machine_model.uuid
  machines    = length(local.microovn_arm64_machine_ids) == 0 ? null : toset(local.microovn_arm64_machine_ids)
  constraints = "arch=arm64"

  charm {
    name    = "microovn"
    channel = var.charm_microovn_channel
    base    = "ubuntu@24.04"
    # Arm64 publishes a different revision than amd64 on the same channel.
  }

  config = var.charm_microovn_config

  endpoint_bindings = var.endpoint_bindings
}

moved {
  from = juju_application.sunbeam-ovn-proxy[0]
  to   = juju_application.sunbeam-ovn-proxy
}

resource "juju_application" "sunbeam-ovn-proxy" {
  name       = "sunbeam-ovn-proxy"
  model_uuid = data.juju_model.machine_model.uuid
  # Deploy on same machine as token distributor
  machines = length(var.token_distributor_machine_ids) == 0 ? null : toset(var.token_distributor_machine_ids)
  units    = length(var.token_distributor_machine_ids) == 0 ? 1 : null

  charm {
    name     = "sunbeam-ovn-proxy"
    channel  = var.charm_sunbeam_ovn_proxy_channel
    base     = "ubuntu@24.04"
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

resource "juju_integration" "role-distributor-microovn" {
  count      = var.role_distributor_application_name != null ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = var.role_distributor_application_name
    endpoint = "role-assignment"
  }

  application {
    name     = juju_application.microovn.name
    endpoint = "role-assignment"
  }
}

resource "juju_integration" "microovn_arm64_microcluster_token_distributor" {
  count      = length(local.microovn_arm64_machine_ids) > 0 ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn_arm64[0].name
    endpoint = "cluster"
  }

  application {
    name     = juju_application.microcluster-token-distributor.name
    endpoint = "microcluster-cluster"
  }
}

resource "juju_integration" "microovn_arm64_openstack_network_agents" {
  count      = length(local.microovn_arm64_machine_ids) > 0 ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn_arm64[0].name
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.openstack-network-agents-arm64[0].name
    endpoint = "juju-info"
  }
}

resource "juju_integration" "role-distributor-microovn-arm64" {
  count = (
    var.role_distributor_application_name != null
    && length(local.microovn_arm64_machine_ids) > 0
  ) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = var.role_distributor_application_name
    endpoint = "role-assignment"
  }

  application {
    name     = juju_application.microovn_arm64[0].name
    endpoint = "role-assignment"
  }
}

resource "juju_integration" "microovn_arm64_certs" {
  count      = (var.ca-offer-url != null && length(local.microovn_arm64_machine_ids) > 0) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn_arm64[0].name
    endpoint = "certificates"
  }

  application {
    offer_url = var.ca-offer-url
  }
}

resource "juju_integration" "microovn_arm64_to_ovn_proxy" {
  count = (
    length(local.microovn_arm64_machine_ids) > 0
    && length(local.microovn_machine_ids) == 0
  ) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn_arm64[0].name
    endpoint = "ovsdb"
  }

  application {
    name     = juju_application.sunbeam-ovn-proxy.name
    endpoint = "ovsdb"
  }
}

resource "juju_integration" "microovn-to-ovn-proxy" {
  count = (
    length(local.microovn_machine_ids) > 0
    || length(local.microovn_arm64_machine_ids) == 0
  ) ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = juju_application.microovn.name
    endpoint = "ovsdb"
  }

  application {
    name     = juju_application.sunbeam-ovn-proxy.name
    endpoint = "ovsdb"
  }
}

moved {
  from = juju_offer.ovsdb-cms[0]
  to   = juju_offer.ovsdb-cms
}

resource "juju_offer" "ovsdb-cms" {
  model_uuid       = data.juju_model.machine_model.uuid
  application_name = juju_application.sunbeam-ovn-proxy.name
  endpoints        = ["ovsdb-cms"]
}

output "microovn-application-name" {
  value = juju_application.microovn.name
}

output "microovn-arm64-application-name" {
  value = try(juju_application.microovn_arm64[0].name, null)
}

output "ovsdb-cms-offer" {
  value = juju_offer.ovsdb-cms.url
}
