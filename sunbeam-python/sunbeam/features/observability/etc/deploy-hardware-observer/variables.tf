# Terraform manifest for deployment of Hardware Observer
#
# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "principal-application-model-uuid" {
  description = "UUID of the Juju model principal application is deployed in"
  type        = string
}

variable "principal-applications" {
  description = "List of the deployed principal applications that hardware-observer integrates with via juju-info"
  type        = list(string)
  default     = []
}

variable "observability-agent-app" {
  description = "Name of the observability agent application to integrate with"
  type        = string
  default     = "opentelemetry-collector"
}

variable "hardware-observer-channel" {
  description = "Channel to use when deploying hardware-observer charm"
  type        = string
  default     = "latest/stable"
}

variable "hardware-observer-revision" {
  description = "Channel revision to use when deploying hardware-observer charm"
  type        = number
  default     = null
}

variable "hardware-observer-base" {
  description = "Base to use when deploying hardware-observer charm"
  type        = string
  default     = "ubuntu@24.04"
}

variable "hardware-observer-config" {
  description = "Config to use when deploying hardware-observer charm"
  type        = map(string)
  default     = {}
}
