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
      version = ">= 0.7.0"
    }
  }

}

provider "juju" {}

data "terraform_remote_state" "openstack" {
  backend = var.openstack-state-backend
  config  = var.openstack-state-config
}

resource "juju_application" "openstack-hypervisor" {
  name  = "openstack-hypervisor"
  trust = false
  model = var.hypervisor_model
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name    = "openstack-hypervisor"
    channel = var.charm_channel
    series  = "jammy"
  }

  config = {
    snap-channel = var.snap_channel
  }

}

resource "juju_integration" "hypervisor-amqp" {
  model = var.hypervisor_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "amqp"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.rabbitmq-offer-url
  }
}

resource "juju_integration" "hypervisor-identity" {
  model = var.hypervisor_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.keystone-offer-url
  }
}

resource "juju_integration" "hypervisor-certs" {
  model = var.hypervisor_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "certificates"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.ca-offer-url
  }
}

resource "juju_integration" "hypervisor-ovn" {
  model = var.hypervisor_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ovsdb-cms"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.ovn-relay-offer-url
  }
}
