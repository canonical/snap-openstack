# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "model" {
  description = "UUID of the machine model to deploy to"
  type        = string
}

variable "backends" {
  description = "Map of storage backend configurations"
  type = map(object({
    principal_application = string
    charm_name           = string
    charm_base           = string
    charm_channel        = string
    charm_revision       = number
    charm_config       = map(string)
    endpoint_bindings    = set(map(string))
    secrets              = map(string)
  }))
  default = {}
}
