# Terraform manifest for deployment of Observability Agent
#
# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.23.1"
    }
  }
}

# To ensure grafana-agent gets removed cleanly before we add opentelemetry-collector
moved {
  from = juju_application.grafana-agent
  to   = juju_application.observability-agent
}

resource "juju_application" "observability-agent" {
  name  = "opentelemetry-collector"
  trust = false
  model = var.principal-application-model

  charm {
    name     = "opentelemetry-collector"
    channel  = var.opentelemetry-collector-channel
    revision = var.opentelemetry-collector-revision
    base     = var.opentelemetry-collector-base
  }

  config = var.opentelemetry-collector-config
}

resource "juju_integration" "observability-agent-integrations" {
  for_each = toset(var.observability-agent-integration-apps)
  model    = var.principal-application-model

  application {
    name     = juju_application.observability-agent.name
    endpoint = "cos-agent"
  }

  application {
    name     = each.value
    endpoint = "cos-agent"
  }
}

resource "juju_integration" "observability-agent-to-cos-prometheus" {
  count = var.receive-remote-write-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.observability-agent.name
  }

  application {
    offer_url = var.receive-remote-write-offer-url
    endpoint  = "receive-remote-write"
  }
}

resource "juju_integration" "observability-agent-to-cos-loki" {
  count = var.logging-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.observability-agent.name
  }

  application {
    offer_url = var.logging-offer-url
  }
}

resource "juju_integration" "observability-agent-to-cos-grafana" {
  count = var.grafana-dashboard-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.observability-agent.name
  }

  application {
    offer_url = var.grafana-dashboard-offer-url
  }
}
