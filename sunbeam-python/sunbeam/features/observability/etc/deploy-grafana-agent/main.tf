# Terraform manifest for deployment of Grafana Agent
#
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
      version = "= 0.11.0"
    }
  }
}

resource "juju_application" "grafana-agent" {
  name  = "grafana-agent"
  trust = false
  model = var.principal-application-model
  units = 0

  charm {
    name     = "grafana-agent"
    channel  = var.grafana-agent-channel
    revision = var.grafana-agent-revision
    base     = "ubuntu@22.04"
  }

  config = var.grafana-agent-config
}

# juju integrate <principal-application>:cos-agent grafana-agent:cos-agent
resource "juju_integration" "principal-application-to-grafana-agent" {
  model = var.principal-application-model

  application {
    name     = juju_application.grafana-agent.name
    endpoint = "cos-agent"
  }

  application {
    name     = var.principal-application
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
