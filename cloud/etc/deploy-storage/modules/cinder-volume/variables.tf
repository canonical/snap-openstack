# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "application_name" {
  description = "Juju application name"
  type        = string
}

variable "charm_channel" {
  description = "Operator channel for cinder_volume deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_revision" {
  description = "Operator channel revision for cinder_volume deployment"
  type        = number
  default     = null
}

variable "charm_config" {
  description = "Operator config for cinder_volume deployment"
  type        = map(string)
  default     = {}
}

variable "cinder_volume_channel" {
  description = "Cinder Volume channel to deploy, not the operator channel"
  default     = null
}

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "machine_model" {
  description = "Model to deploy to"
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for cinder_volume"
  type        = set(map(string))
  default     = null
}

variable "keystone-offer-url" {
  description = "Offer URL for openstack keystone endpoints"
  type        = string
  default     = null
}

variable "amqp-offer-url" {
  description = "Offer URL for amqp"
  type        = string
  default     = null
}

variable "database-offer-url" {
  description = "Offer URL for database"
  type        = string
  default     = null
}
