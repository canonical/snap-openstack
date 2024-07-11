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
      version = "= 0.13.0"
    }
  }

}

provider "juju" {}

data "juju_model" "machine_model" {
  name = var.machine_model
}

resource "juju_application" "k8s" {
  name  = "k8s"
  model = data.juju_model.machine_model.name
  units = length(var.machine_ids) # need to manage the number of units

  charm {
    name     = "k8s"
    channel  = var.k8s_channel
    revision = var.k8s_revision
    base     = "ubuntu@22.04"
  }

  config = var.k8s_config
  endpoint_bindings = var.endpoint_bindings
}
