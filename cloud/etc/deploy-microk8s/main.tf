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
    channel              = var.microk8s_channel
    addons               = join(" ", [for key, value in var.addons : "${key}:${value}"])
    disable_cert_reissue = true
    containerd_env       = var.containerd_env
  }
}
