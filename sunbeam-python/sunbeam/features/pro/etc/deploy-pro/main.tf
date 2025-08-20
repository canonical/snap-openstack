# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {

  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.20.0"
    }
  }

}

provider "juju" {}

resource "juju_application" "ubuntu_pro" {
  count = var.token != "" ? 1 : 0
  name  = "ubuntu-pro"
  model = var.machine-model

  charm {
    name    = "ubuntu-advantage"
    channel = var.ubuntu-advantage-channel
    base    = "ubuntu@24.04"
  }

  config = {
    token = var.token
  }
}

resource "juju_integration" "juju_info" {
  count = var.token != "" ? 1 : 0
  model = var.machine-model

  application {
    name     = "sunbeam-machine"
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.ubuntu_pro[count.index].name
    endpoint = "juju-info"
  }
}
