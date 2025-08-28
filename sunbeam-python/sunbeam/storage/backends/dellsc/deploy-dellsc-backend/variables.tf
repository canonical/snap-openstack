# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "machine_model" {
  description = "Name of the machine model to deploy to"
  type        = string
}

variable "charm_dellsc_name" {
  description = "Name of the Dell Storage Center charm"
  type        = string
  default     = "cinder-volume-dellsc"
}

variable "charm_dellsc_base" {
  description = "Base for the Dell Storage Center charm"
  type        = string
  default     = "ubuntu@24.04"
}

variable "charm_dellsc_channel" {
  description = "Operator channel for Dell Storage Center backend deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_dellsc_endpoint" {
  description = "Endpoint name for Dell Storage Center backend integration"
  type        = string
  default     = "cinder-volume"
}

variable "charm_dellsc_revision" {
  description = "Operator channel revision for Dell Storage Center backend deployment"
  type        = number
  default     = null
}

variable "dellsc_backends" {
  description = "Map of Dell Storage Center backend configurations"
  type = map(object({
    charm_config = map(string)
    
    # Main array credentials (required)
    san_username = string
    san_password = string
    
    # Secondary array credentials (optional, for dual DSM)
    secondary_san_username = optional(string, "")
    secondary_san_password = optional(string, "")
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
