# Terraform manifest for deployment of Grafana Agent
#
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

resource "juju_application" "grafana-agent" {
  name  = "grafana-agent"
  trust = false
  model = var.principal-application-model

  charm {
    name     = "grafana-agent"
    channel  = var.grafana-agent-channel
    revision = var.grafana-agent-revision
    base     = var.grafana-agent-base
  }

  config = var.grafana-agent-config
}

# juju integrate <principal-application>:cos-agent grafana-agent:cos-agent
resource "juju_integration" "grafana_agent_integrations" {
  for_each = toset(var.grafana-agent-integration-apps)
  model    = var.principal-application-model

  application {
    name     = juju_application.grafana-agent.name
    endpoint = "cos-agent"
  }

  application {
    name     = each.value
    endpoint = "cos-agent"
  }
}

# juju integrate grafana-agent cos.prometheus-receive-remote-write
resource "juju_integration" "grafana-agent-to-cos-prometheus" {
  count = var.receive-remote-write-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.grafana-agent.name
  }

  application {
    offer_url = var.receive-remote-write-offer-url
    endpoint  = "receive-remote-write"
  }
}

# juju integrate grafana-agent cos.loki-logging
resource "juju_integration" "grafana-agent-to-cos-loki" {
  count = var.logging-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.grafana-agent.name
  }

  application {
    offer_url = var.logging-offer-url
  }
}

# juju integrate grafana-agent cos.grafana-dashboards
resource "juju_integration" "grafana-agent-to-cos-grafana" {
  count = var.grafana-dashboard-offer-url != null ? 1 : 0
  model = var.principal-application-model

  application {
    name = juju_application.grafana-agent.name
  }

  application {
    offer_url = var.grafana-dashboard-offer-url
  }
}
