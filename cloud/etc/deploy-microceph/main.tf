# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

terraform {

  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.17.0"
    }
  }

}

provider "juju" {}

data "juju_model" "machine_model" {
  name = var.machine_model
}

resource "juju_application" "microceph" {
  name  = "microceph"
  trust = true
  model = data.juju_model.machine_model.name
  units = length(var.machine_ids) # need to manage the number of units

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
  application_name = juju_application.microceph.name
  endpoint         = "ceph"
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
