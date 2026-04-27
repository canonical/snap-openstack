# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 1.3.1"
    }
  }
}

provider "juju" {}

data "juju_model" "machine_model" {
  uuid = var.machine_model_uuid
}

resource "juju_application" "ubuntu_pro" {
  count      = var.token != "" ? 1 : 0
  name       = "ubuntu-pro"
  model_uuid = data.juju_model.machine_model.uuid

  charm {
    name    = "ubuntu-advantage"
    channel = var.ubuntu-advantage-channel
    base    = "ubuntu@26.04"
  }

  config = {
    token = var.token
  }
}

resource "juju_integration" "juju_info" {
  count      = var.token != "" ? 1 : 0
  model_uuid = data.juju_model.machine_model.uuid

  application {
    name     = "sunbeam-machine"
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.ubuntu_pro[count.index].name
    endpoint = "juju-info"
  }
}
