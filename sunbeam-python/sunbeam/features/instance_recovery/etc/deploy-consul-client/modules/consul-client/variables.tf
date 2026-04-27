# Terraform manifest for deployment of Consul client
#
# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "name" {
  description = "Name of the deployed Consul client operator"
  type        = string
  default     = "consul"
}

variable "channel" {
  description = "Channel to use when deploying consul client machine charm"
  type        = string
  default     = "latest/edge"
}

variable "revision" {
  description = "Channel revision to use when deploying consul client machine charm"
  type        = number
  default     = null
}

variable "base" {
  description = "Operator base"
  type        = string
  default     = "ubuntu@26.04"
}

variable "resource-configs" {
  description = "Config to use when deploying consul client machine charm"
  type        = map(string)
  default     = {}
}

variable "endpoint-bindings" {
  description = "Endpoint bindings for consul client"
  type        = set(map(string))
  default     = null
}

variable "principal-application" {
  description = "Name of the deployed principal application that integrates with consul-client"
  type        = string
  default     = null
}

variable "principal-application-model-uuid" {
  description = "UUID of the Juju model principal application is deployed in"
  type        = string
}

variable "consul-cluster-offer-url" {
  description = "Consul cluster offer url for client to join the cluster"
  type        = string
  default     = null
}
