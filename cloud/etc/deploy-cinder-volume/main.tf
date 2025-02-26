# Copyright (c) 2025 Canonical Ltd.
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

resource "juju_application" "cinder-volume" {
  name  = "cinder-volume"
  model = data.juju_model.machine_model.name
  units = length(var.machine_ids)

  charm {
    name     = "cinder-volume"
    channel  = var.charm_cinder_volume_channel
    revision = var.charm_cinder_volume_revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    snap-channel = var.cinder_volume_channel
  }, var.charm_cinder_volume_config)
  endpoint_bindings = var.endpoint_bindings
}

resource "juju_offer" "storage-backend-offer" {
  application_name = juju_application.cinder-volume.name
  endpoint         = "storage-backend"
  model            = data.juju_model.machine_model.name
}

resource "juju_integration" "cinder-volume-identity" {
  count = (var.keystone-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "identity-credentials"
  }

  application {
    offer_url = var.keystone-offer-url
  }
}

resource "juju_integration" "cinder-volume-amqp" {
  count = (var.amqp-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "amqp"
  }

  application {
    offer_url = var.amqp-offer-url
  }
}

resource "juju_integration" "cinder-volume-database" {
  count = (var.database-offer-url != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "database"
  }

  application {
    offer_url = var.database-offer-url
  }
}


resource "juju_application" "cinder-volume-ceph" {
  name  = "cinder-volume-ceph"
  model = data.juju_model.machine_model.name

  # charm is subordinate
  units = 0
  charm {
    name     = "cinder-volume-ceph"
    channel  = var.charm_cinder_volume_ceph_channel
    revision = var.charm_cinder_volume_ceph_revision
    base     = "ubuntu@24.04"
  }

  config            = var.charm_cinder_volume_ceph_config
  endpoint_bindings = var.cinder_volume_ceph_endpoint_bindings
}

resource "juju_integration" "cinder-volume-ceph-to-cinder-volule" {
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume-ceph.name
    endpoint = "cinder-volume"
  }

  application {
    name     = juju_application.cinder-volume.name
    endpoint = "cinder-volume"
  }
}

resource "juju_integration" "cinder-volume-ceph-to-ceph" {
  count = (var.ceph-application-name != null) ? 1 : 0
  model = var.machine_model

  application {
    name     = juju_application.cinder-volume-ceph.name
    endpoint = "ceph"
  }

  application {
    name     = var.ceph-application-name
    endpoint = "ceph"
  }
}


output "cinder-volume-ceph-application-name" {
  value = juju_application.cinder-volume-ceph.name
}
