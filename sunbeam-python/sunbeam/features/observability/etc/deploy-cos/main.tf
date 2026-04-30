# Terraform manifest for deployment of COS Lite
# Based on https://github.com/canonical/cos-lite-bundle/blob/a39ee6b04b6833f44cfe913ee00e2853cb36428b/bundle.yaml.j2
#
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

resource "juju_model" "cos" {
  name = var.model

  cloud {
    name   = var.cloud
    region = var.region
  }

  credential = var.credential
  config     = var.config
}

resource "juju_application" "traefik" {
  name       = "traefik"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "traefik-k8s"
    channel  = var.traefik-channel == null ? var.cos-channel : var.traefik-channel
    revision = var.traefik-revision
    base     = "ubuntu@20.04"
  }

  config = var.traefik-config
  units  = var.ingress-scale
}

resource "juju_application" "alertmanager" {
  name       = "alertmanager"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "alertmanager-k8s"
    channel  = var.alertmanager-channel == null ? var.cos-channel : var.alertmanager-channel
    revision = var.alertmanager-revision
    base     = "ubuntu@20.04"
  }

  config = var.alertmanager-config
  units  = var.alertmanager-scale

  storage_directives = var.alertmanager-storage
}

resource "juju_application" "prometheus" {
  name       = "prometheus"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "prometheus-k8s"
    channel  = var.prometheus-channel == null ? var.cos-channel : var.prometheus-channel
    revision = var.prometheus-revision
    base     = "ubuntu@20.04"
  }

  config = var.prometheus-config
  units  = var.prometheus-scale

  storage_directives = var.prometheus-storage
}

resource "juju_application" "grafana" {
  name       = "grafana"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "grafana-k8s"
    channel  = var.grafana-channel == null ? var.cos-channel : var.grafana-channel
    revision = var.grafana-revision
    base     = "ubuntu@20.04"
  }

  config = var.grafana-config
  units  = var.grafana-scale

  storage_directives = var.grafana-storage
}

resource "juju_application" "catalogue" {
  name       = "catalogue"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "catalogue-k8s"
    channel  = var.catalogue-channel == null ? var.cos-channel : var.catalogue-channel
    revision = var.catalogue-revision
    base     = "ubuntu@20.04"
  }

  config = merge({
    title       = "Canonical Observability Stack"
    tagline     = "Model-driven Observability Stack deployed with a single command."
    description = " Canonical Observability Stack Lite, or COS Lite, is a light-weight, highly-integrated, Juju-based observability suite running on Kubernetes."
  }, var.catalogue-config)

  units = var.catalogue-scale
}

resource "juju_application" "loki" {
  name       = "loki"
  trust      = true
  model_uuid = juju_model.cos.uuid

  charm {
    name     = "loki-k8s"
    channel  = var.loki-channel == null ? var.cos-channel : var.loki-channel
    revision = var.loki-revision
    base     = "ubuntu@20.04"
  }

  config = var.loki-config
  units  = var.loki-scale

  storage_directives = var.loki-storage
}

# juju integrate traefik prometheus
resource "juju_integration" "traefik-to-prometheus" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.traefik.name
    endpoint = "ingress-per-unit"
  }

  application {
    name     = juju_application.prometheus.name
    endpoint = "ingress"
  }
}

# juju integrate traefik loki
resource "juju_integration" "traefik-to-loki" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.traefik.name
    endpoint = "ingress-per-unit"
  }

  application {
    name     = juju_application.loki.name
    endpoint = "ingress"
  }
}

# juju integrate traefik grafana
resource "juju_integration" "traefik-to-grafana" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.traefik.name
    endpoint = "traefik-route"
  }

  application {
    name     = juju_application.grafana.name
    endpoint = "ingress"
  }
}

# juju integrate traefik alertmanager
resource "juju_integration" "traefik-to-alertmanager" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.traefik.name
    endpoint = "ingress"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "ingress"
  }
}

# juju integrate prometheus alertmanager
resource "juju_integration" "prometheus-to-alertmanager" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.prometheus.name
    endpoint = "alertmanager"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "alerting"
  }
}

# juju integrate grafana prometheus on interface grafana-source
resource "juju_integration" "grafana-to-prometheus-on-grafana-source" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-source"
  }

  application {
    name     = juju_application.prometheus.name
    endpoint = "grafana-source"
  }
}

# juju integrate grafana loki on interface grafana-source
resource "juju_integration" "grafana-to-loki-on-grafana-source" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-source"
  }

  application {
    name     = juju_application.loki.name
    endpoint = "grafana-source"
  }
}

# juju integrate grafana alertmanager on interface grafana-source
resource "juju_integration" "grafana-to-alertmanager-on-grafana-source" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-source"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "grafana-source"
  }
}

# juju integrate loki alertmanager
resource "juju_integration" "loki-to-alertmanager" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.loki.name
    endpoint = "alertmanager"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "alerting"
  }
}

# COS monitoring

# juju integrate prometheus traefik on interface metrics-endpoint
resource "juju_integration" "prometheus-to-traefik-on-metrics-endpoint" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.prometheus.name
    endpoint = "metrics-endpoint"
  }

  application {
    name     = juju_application.traefik.name
    endpoint = "metrics-endpoint"
  }
}

# juju integrate prometheus alertmanager on interface metrics-endpoint
resource "juju_integration" "prometheus-to-alertmanager-on-metrics-endpoint" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.prometheus.name
    endpoint = "metrics-endpoint"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "self-metrics-endpoint"
  }
}

# juju integrate prometheus loki on interface metrics-endpoint
resource "juju_integration" "prometheus-to-loki-on-metrics-endpoint" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.prometheus.name
    endpoint = "metrics-endpoint"
  }

  application {
    name     = juju_application.loki.name
    endpoint = "metrics-endpoint"
  }
}

# juju integrate prometheus grafana on interface metrics-endpoint
resource "juju_integration" "prometheus-to-grafana-on-metrics-endpoint" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.prometheus.name
    endpoint = "metrics-endpoint"
  }

  application {
    name     = juju_application.grafana.name
    endpoint = "metrics-endpoint"
  }
}

# juju integrate grafana to loki on interface grafana-dashboard
resource "juju_integration" "grafana-to-loki-on-grafana-dashboard" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-dashboard"
  }

  application {
    name     = juju_application.loki.name
    endpoint = "grafana-dashboard"
  }
}

# juju integrate grafana to prometheus on interface grafana-dashboard
resource "juju_integration" "grafana-to-prometheus-on-grafana-dashboard" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-dashboard"
  }

  application {
    name     = juju_application.prometheus.name
    endpoint = "grafana-dashboard"
  }
}

# juju integrate grafana to alertmanager on interface grafana-dashboard
resource "juju_integration" "grafana-to-alertmanager-on-grafana-dashboard" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.grafana.name
    endpoint = "grafana-dashboard"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "grafana-dashboard"
  }
}

# Service Catalogue

# juju integrate catalogue to traefik
resource "juju_integration" "catalogue-to-traefik" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.catalogue.name
    endpoint = "ingress"
  }

  application {
    name     = juju_application.traefik.name
    endpoint = "ingress"
  }
}

# juju integrate catalogue to grafana
resource "juju_integration" "catalogue-to-grafana" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.catalogue.name
    endpoint = "catalogue"
  }

  application {
    name     = juju_application.grafana.name
    endpoint = "catalogue"
  }
}

# juju integrate catalogue to prometheus
resource "juju_integration" "catalogue-to-prometheus" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.catalogue.name
    endpoint = "catalogue"
  }

  application {
    name     = juju_application.prometheus.name
    endpoint = "catalogue"
  }
}

# juju integrate catalogue to alertmanager
resource "juju_integration" "catalogue-to-alertmanager" {
  model_uuid = juju_model.cos.uuid

  application {
    name     = juju_application.catalogue.name
    endpoint = "catalogue"
  }

  application {
    name     = juju_application.alertmanager.name
    endpoint = "catalogue"
  }
}

# juju offer prometheus:metrics-endpoint
resource "juju_offer" "prometheus-metrics-offer" {
  name             = "prometheus-scrape"
  model_uuid       = juju_model.cos.uuid
  application_name = juju_application.prometheus.name
  endpoints        = ["metrics-endpoint"]
}

# juju offer prometheus:receive-remote-write
resource "juju_offer" "prometheus-receive-remote-write-offer" {
  name             = "prometheus-receive-remote-write"
  model_uuid       = juju_model.cos.uuid
  application_name = juju_application.prometheus.name
  endpoints        = ["receive-remote-write"]
}

# juju offer loki:logging
resource "juju_offer" "loki-logging-offer" {
  name             = "loki-logging"
  model_uuid       = juju_model.cos.uuid
  application_name = juju_application.loki.name
  endpoints        = ["logging"]
}

# juju offer grafana:dashboard
resource "juju_offer" "grafana-dashboard-offer" {
  name             = "grafana-dashboards"
  model_uuid       = juju_model.cos.uuid
  application_name = juju_application.grafana.name
  endpoints        = ["grafana-dashboard"]
}

# juju offer alertmanager:karma-dashboard
resource "juju_offer" "alertmanager-karma-dashboard-offer" {
  name             = "alertmanager-karma-dashboard"
  model_uuid       = juju_model.cos.uuid
  application_name = juju_application.alertmanager.name
  endpoints        = ["karma-dashboard"]
}
