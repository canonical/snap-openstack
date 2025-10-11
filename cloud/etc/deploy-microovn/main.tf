# microovn.tf
terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.23.0"
    }
  }
}

provider "juju" {}

resource "juju_application" "openstack-network-agents" {
  name  = "openstack-network-agents"
  trust = true
  model = var.machine_model

  charm {
    name    = "openstack-network-agents"
    channel = var.charm_openstack_network_agents_channel
    base    = "ubuntu@24.04"
  }
}

resource "juju_application" "microcluster-token-distributor" {
  name  = "microcluster-token-distributor"
  trust = true
  model = var.machine_model
  machines = length(var.token_distributor_machine_ids) == 0 ? null : toset(var.token_distributor_machine_ids)
  units    = length(var.token_distributor_machine_ids) == 0 ? 1    : null

  charm {
    name    = "microcluster-token-distributor"
    channel = var.charm_microcluster_token_distributor_channel
    base    = "ubuntu@24.04"
  }
}

resource "juju_application" "microovn" {
  name  = "microovn"
  trust = true
  model = var.machine_model
  machines = length(var.microovn_machine_ids) == 0 ? null : toset(var.microovn_machine_ids)
  units    = length(var.microovn_machine_ids) == 0 ? 1    : null

  charm {
    name    = "microovn"
    channel = var.charm_microovn_channel
    base    = "ubuntu@24.04"
  }

  endpoint_bindings = var.endpoint_bindings
}

resource "juju_integration" "microovn-microcluster-token-distributor" {
  model = var.machine_model

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
  count = (var.ca-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.microovn.name
    endpoint = "certificates"
  }

  application {
    offer_url = var.ca-offer-url
  }
}

resource "juju_integration" "microovn-ovsdb-cms" {
  count = (var.ovn-relay-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.microovn.name
    endpoint = "ovsdb-external"
  }

  application {
    offer_url = var.ovn-relay-offer-url
  }
}

resource "juju_integration" "microovn-openstack-network-agents" {
  model = var.machine_model

  application {
    name     = juju_application.microovn.name
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.openstack-network-agents.name
    endpoint = "juju-info"
  }
}

output "microovn-application-name" {
  value = juju_application.microovn.name
}
