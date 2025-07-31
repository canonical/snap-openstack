# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "charm_channel" {
  description = "Charm channel to deploy sunbeam-machine charm from"
  type        = string
  default     = "2024.1/stable"
}

variable "charm_revision" {
  description = "Charm channel revision to deploy sunbeam-machine charm from"
  type        = number
  default     = null
}

variable "charm_config" {
  description = "Charm config to deploy sunbeam-machine charm from"
  type        = map(string)
  default     = {}
}

variable "machine_model" {
  description = "Name of model to deploy sunbeam-machine into."
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for sunbeam-machine"
  type        = set(map(string))
  default     = null
}

variable "charm_epa_orchestrator_channel" {
  description = "Operator channel for epa-orchestrator deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_epa_orchestrator_revision" {
  description = "Operator revision for epa-orchestrator deployment"
  type        = number
  default     = null
}

variable "charm_epa_orchestrator_config" {
  description = "Operator config for epa-orchestrator deployment"
  type        = map(string)
  default     = {}
}

variable "epa_orchestrator_endpoint_bindings" {
  description = "Endpoint bindings for epa-orchestrator"
  type        = set(map(string))
  default     = null
}
