# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "model" {
  description = "Name of the machine model to deploy to"
  type        = string
}

variable "principal_application" {
  description = "Name of the principal application to integrate with"
  type        = string
  default     = "cinder-volume"
}

variable "charm_name" {
  description = "Name of the Storage charm"
  type        = string
}

variable "charm_base" {
  description = "Base for the Storage charm"
  type        = string
  default     = "ubuntu@24.04"
}

variable "charm_channel" {
  description = "Operator channel for Storage backend deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_revision" {
  description = "Operator channel revision for Storage backend deployment"
  type        = number
  default     = null
}

variable "name" {
  description = "Name of the backend"
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for the applications"
  type        = set(map(string))
  default     = null
}

variable "charm_config" {
  description = "Operator config for the Storage backend deployment"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Map of secret names to create. The key is the config option name, the value is a map of secret-key -> secret-value."
  type        = map(map(string))
  default     = {}
}
