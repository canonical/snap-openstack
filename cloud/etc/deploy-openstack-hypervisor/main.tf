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
      version = "= 0.11.0"
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
  model = var.machine_model
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name     = "openstack-hypervisor"
    channel  = var.charm_channel
    revision = var.charm_revision
    base    = "ubuntu@22.04"
  }

  config = merge({
    snap-channel = var.snap_channel
  }, var.charm_config)

}

resource "juju_integration" "hypervisor-amqp" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "amqp"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.rabbitmq-offer-url
  }
}

resource "juju_integration" "hypervisor-identity" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.keystone-offer-url
  }
}

resource "juju_integration" "hypervisor-cert-distributor" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "receive-ca-cert"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.cert-distributor-offer-url
  }
}

resource "juju_integration" "hypervisor-certs" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "certificates"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.ca-offer-url
  }
}

resource "juju_integration" "hypervisor-ovn" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ovsdb-cms"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.ovn-relay-offer-url
  }
}

resource "juju_integration" "hypervisor-ceilometer" {
  count = try(data.terraform_remote_state.openstack.outputs.ceilometer-offer-url, null) != null ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ceilometer-service"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.ceilometer-offer-url
  }
}

resource "juju_integration" "hypervisor-cinder-ceph" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "ceph-access"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.cinder-ceph-offer-url
  }
}

resource "juju_integration" "hypervisor-nova-controller" {
  model = var.machine_model

  application {
    name     = juju_application.openstack-hypervisor.name
    endpoint = "nova-service"
  }

  application {
    offer_url = data.terraform_remote_state.openstack.outputs.nova-offer-url
  }
}
