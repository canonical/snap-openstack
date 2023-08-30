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
      version = ">= 0.8.0"
    }
  }

}

provider "juju" {}

resource "juju_application" "vault" {
  model = var.model
  name  = "vault"
  trust = true

  charm {
    name     = "vault-k8s"
    channel  = var.channel
    revision = 11
    series   = "jammy"
  }

  units = 1
}