# Terraform manifest for deployment of COS Lite
#
# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "model" {
  description = "Name of Juju model to use for deployment"
  type        = string
  default     = "cos"
}

variable "cloud" {
  description = "Name of K8S cloud to use for deployment"
  type        = string
  default     = "k8s"
}

variable "region" {
  description = "The region of K8S cloud to use for deployment"
  type        = string
  default     = "localhost"
}

# https://github.com/juju/terraform-provider-juju/issues/147
variable "credential" {
  description = "Name of credential to use for deployment"
  type        = string
  default     = ""
}

variable "config" {
  description = "Set configuration on model"
  type        = map(string)
  default     = {}
}

variable "cos-channel" {
  description = "Operator channel for COS Lite deployment"
  type        = string
  default     = "latest/stable"
}

variable "traefik-channel" {
  description = "Operator channel for COS Lite Traefik deployment"
  type        = string
  default     = "latest/stable"
}

variable "traefik-revision" {
  description = "Operator channel revision for COS Lite Traefik deployment"
  type        = number
  default     = null
}

variable "traefik-config" {
  description = "Operator config for COS Lite Traefik deployment"
  type        = map(string)
  default     = {}
}

variable "alertmanager-channel" {
  description = "Operator channel for COS Lite Alert Manager deployment"
  type        = string
  default     = "latest/stable"
}

variable "alertmanager-revision" {
  description = "Operator channel revision for COS Lite Alert Manager deployment"
  type        = number
  default     = null
}

variable "alertmanager-config" {
  description = "Operator config for COS Lite Alert Manager deployment"
  type        = map(string)
  default     = {}
}

variable "prometheus-channel" {
  description = "Operator channel for COS Lite Prometheus deployment"
  type        = string
  default     = "latest/stable"
}

variable "prometheus-revision" {
  description = "Operator channel revision for COS Lite Prometheus deployment"
  type        = number
  default     = null
}

variable "prometheus-config" {
  description = "Operator config for COS Lite Prometheus deployment"
  type        = map(string)
  default     = {}
}

variable "grafana-channel" {
  description = "Operator channel for COS Lite Grafana deployment"
  type        = string
  default     = "latest/stable"
}

variable "grafana-revision" {
  description = "Operator channel revision for COS Lite Grafana deployment"
  type        = number
  default     = null
}

variable "grafana-config" {
  description = "Operator config for COS Lite Grafana deployment"
  type        = map(string)
  default     = {}
}

variable "catalogue-channel" {
  description = "Operator channel for COS Lite Catalogue deployment"
  type        = string
  default     = "latest/stable"
}

variable "catalogue-revision" {
  description = "Operator channel revision for COS Lite Catalogue deployment"
  type        = number
  default     = null
}

variable "catalogue-config" {
  description = "Operator config for COS Lite Catalogue deployment"
  type        = map(string)
  default     = {}
}

variable "loki-channel" {
  description = "Operator channel for COS Lite Loki deployment"
  type        = string
  default     = "latest/stable"
}

variable "loki-revision" {
  description = "Operator channel revision for COS Lite Loki deployment"
  type        = number
  default     = null
}

variable "loki-config" {
  description = "Operator config for COS Lite Loki deployment"
  type        = map(string)
  default     = {}
}

variable "ingress-scale" {
  description = "Scale of ingress deployment"
  type        = number
  default     = 1
}

variable "alertmanager-scale" {
  description = "Scale of alertmanagement deployment"
  type        = number
  default     = 1
}

variable "prometheus-scale" {
  description = "Scale of prometheus deployment"
  type        = number
  default     = 1
}

variable "grafana-scale" {
  description = "Scale of grafana deployment"
  type        = number
  default     = 1
}

variable "catalogue-scale" {
  description = "Scale of catalogue deployment"
  type        = number
  default     = 1
}

variable "loki-scale" {
  description = "Scale of loki deployment"
  type        = number
  default     = 1
}

variable "prometheus-storage" {
  description = "Storage directives for Prometheus"
  type        = map(string)
  default = {
    database = "20G"
  }
}

variable "loki-storage" {
  description = "Storage directives for Loki"
  type        = map(string)
  default = {
    active-index-directory = "2G"
    loki-chunks            = "5G"
  }
}

variable "grafana-storage" {
  description = "Storage directives for Grafana"
  type        = map(string)
  default = {
    database = "1G"
  }
}

variable "alertmanager-storage" {
  description = "Storage directives for Alertmanager"
  type        = map(string)
  default = {
    data = "1G"
  }
}
