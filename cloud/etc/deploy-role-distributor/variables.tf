# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm_role_distributor_channel" {
  description = "Operator channel for role-distributor deployment"
  type        = string
  default     = "latest/stable"
}

variable "charm_role_distributor_revision" {
  description = "Operator channel revision for role-distributor deployment"
  type        = number
  default     = null
}

variable "charm_role_distributor_config" {
  description = "Operator config for role-distributor deployment"
  type        = map(string)
  default     = {}
}

variable "role_distributor_machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "machine_ids" {
  description = "Compatibility variable ignored by this plan"
  type        = list(string)
  default     = []
}

variable "machine_model_uuid" {
  description = "UUID of Juju model to use for deployment"
  type        = string
}
