# Terraform manifest for deployment of Observability Agent
#
# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "observability-agent-integration-apps" {
  description = "List of the deployed principal applications that integrate with opentelemetry collector"
  type        = list(string)
  default     = []
}

variable "principal-application-model" {
  description = "Name of the model principal application is deployed in"
  default     = "controller"
}

variable "opentelemetry-collector-channel" {
  description = "Channel to use when deploying opentelemetry collector machine charm"
  type        = string
  default     = "2/stable"
}

variable "opentelemetry-collector-revision" {
  description = "Channel revision to use when deploying opentelemetry collector machine charm"
  type        = number
  default     = null
}

variable "opentelemetry-collector-base" {
  description = "Base to use when deploying opentelemetry collector machine charm"
  type        = string
  default     = "ubuntu@24.04"
}

variable "opentelemetry-collector-config" {
  description = "Config to use when deploying opentelemetry collector machine charm"
  type        = map(string)
  default     = {}
}

variable "receive-remote-write-offer-url" {
  description = "Offer URL from prometheus-k8s:receive-remote-write application"
  type        = string
  default     = null
}

variable "grafana-dashboard-offer-url" {
  description = "Offer URL from grafana-k8s:grafana-dashboard application"
  type        = string
  default     = null
}

variable "logging-offer-url" {
  description = "Offer URL from loki-k8s:logging application"
  type        = string
  default     = null
}
