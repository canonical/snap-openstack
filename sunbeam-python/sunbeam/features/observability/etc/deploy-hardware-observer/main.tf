# Terraform manifest for deployment of Hardware Observer
#
# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 1.5.6"
    }
  }
}

provider "juju" {}

data "juju_model" "principal_application_model" {
  uuid = var.principal-application-model-uuid
}

resource "juju_application" "hardware-observer" {
  name       = "hardware-observer"
  model_uuid = data.juju_model.principal_application_model.uuid

  charm {
    name     = "hardware-observer"
    channel  = var.hardware-observer-channel
    revision = var.hardware-observer-revision
    base     = var.hardware-observer-base
  }

  config = var.hardware-observer-config
}

resource "juju_integration" "hardware-observer-to-observability-agent" {
  model_uuid = data.juju_model.principal_application_model.uuid

  application {
    name     = juju_application.hardware-observer.name
    endpoint = "cos-agent"
  }

  application {
    name     = var.observability-agent-app
    endpoint = "cos-agent"
  }
}

resource "juju_integration" "hardware-observer-principal-integrations" {
  for_each   = toset(var.principal-applications)
  model_uuid = data.juju_model.principal_application_model.uuid

  application {
    name     = juju_application.hardware-observer.name
    endpoint = "general-info"
  }

  application {
    name     = each.value
    endpoint = "juju-info"
  }
}
