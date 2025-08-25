# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm-manila-data-channel" {
  description = "Operator channel for manila_data deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm-manila-data-revision" {
  description = "Operator channel revision for manila_data deployment"
  type        = number
  default     = null
}

variable "charm-manila-data-config" {
  description = "Operator config for manila_data deployment"
  type        = map(string)
  default     = {}
}

variable "charm_manila_data_config" {
  description = "Operator config for manila_data deployment"
  type        = map(string)
  default     = {}
}

variable "manila-data-channel" {
  description = "Manila Data snap channel to deploy, not the operator channel"
  default     = "2024.1/edge"
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
  description = "Endpoint bindings for manila_data"
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
