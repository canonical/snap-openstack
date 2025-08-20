# Terraform module for deployment of Consul client
#
# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.20.0"
    }
  }
}

resource "juju_application" "consul-client" {
  name  = var.name
  trust = false
  model = var.principal-application-model

  charm {
    name     = "consul-client"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config            = var.resource-configs
  endpoint_bindings = var.endpoint-bindings
}

# juju integrate <principal-application>:juju-info consul-client:general-info
resource "juju_integration" "principal-application-to-consul-client" {
  model = var.principal-application-model

  application {
    name     = juju_application.consul-client.name
    endpoint = "juju-info"
  }

  application {
    name     = var.principal-application
    endpoint = "juju-info"
  }
}

# juju integrate <consul-client>:consul-cluster consul-cluster-offer-url
resource "juju_integration" "consul-client-to-consul-server" {
  count = var.consul-cluster-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name     = juju_application.consul-client.name
    endpoint = "consul-cluster"
  }

  application {
    offer_url = var.consul-cluster-offer-url
  }
}
