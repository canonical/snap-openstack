# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "machine_model" {
  description = "Name of the machine model to deploy to"
  type        = string
}

variable "charm_purestorage_name" {
  description = "Name of the Pure Storage charm"
  type        = string
  default     = "cinder-volume-purestorage"
}

variable "charm_purestorage_base" {
  description = "Base for the Pure Storage charm"
  type        = string
  default     = "ubuntu@24.04"
}

variable "charm_purestorage_channel" {
  description = "Operator channel for Pure Storage backend deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_purestorage_endpoint" {
  description = "Endpoint name for Pure Storage backend integration"
  type        = string
  default     = "cinder-volume"
}

variable "charm_purestorage_revision" {
  description = "Operator channel revision for Pure Storage backend deployment"
  type        = number
  default     = null
}

variable "purestorage_backends" {
  description = "Map of Pure Storage backend configurations"
  type = map(object({
    charm_config = map(string)
  }))
  default = {}
}

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for the applications"
  type        = set(map(string))
  default     = null
}

