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
      version = "= 0.8.0"
    }
  }

}

provider "juju" {}

data "juju_model" "controller" {
  name = "controller"
}

resource "juju_application" "microk8s" {
  name  = "microk8s"
  trust = true
  model = data.juju_model.controller.name
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name    = "microk8s"
    channel = var.charm_microk8s_channel
    series  = "jammy"
  }

  config = {
    automatic_certificate_reissue = false
    hostpath_storage              = true
  }
}

resource "juju_model" "addons" {
  count = var.enable-addons ? 1 : 0
  name  = var.addons-model

  cloud {
    name   = var.cloud
    region = "localhost"
  }

  credential = var.credential
  config     = var.config
}

resource "juju_application" "coredns" {
  count = var.enable-addons ? 1 : 0
  name  = "coredns"
  trust = true
  model = juju_model.addons[count.index].name
  units = var.coredns-ha-scale

  charm {
    name    = "coredns"
    channel = var.charm-coredns-channel
    series  = "jammy"
  }
}

# juju_offer.coredns_offer will be created
resource "juju_offer" "coredns_offer" {
  count            = var.enable-addons ? 1 : 0
  application_name = juju_application.coredns[count.index].name
  endpoint         = "dns-provider"
  model            = juju_model.addons[count.index].name
}

# juju integrate coredns microk8s
resource "juju_integration" "microk8s-to-coredns" {
  count = var.enable-addons ? 1 : 0
  model = data.juju_model.controller.name

  application {
    name     = juju_application.microk8s.name
    endpoint = "dns"
  }

  application {
    offer_url = juju_offer.coredns_offer[count.index].url
  }
}

resource "juju_application" "metallb" {
  count = var.enable-addons ? 1 : 0
  name  = "metallb"
  trust = true
  model = juju_model.addons[count.index].name
  # Not possible to scale, so hardcoded to 1
  units = 1

  charm {
    name    = "metallb"
    channel = var.charm-metallb-channel
    series  = "jammy"
  }

  config = {
    iprange = var.metallb-iprange
  }
}
