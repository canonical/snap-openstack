# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "ubuntu-pro-channel" {
  description = "Channel to use for deployment of ubuntu-pro charm"
  type        = string
  default     = "latest/stable"
}

variable "ubuntu-pro-revision" {
  description = "Revision number of ubuntu-pro charm"
  type        = number
  default     = null
}

variable "ubuntu-pro-config" {
  description = "Config options of the ubuntu-pro charm, such as contract_url"
  type        = map(string)
  default     = {}
}

variable "machine_model_uuid" {
  description = "UUID of Juju model to deploy ubuntu-pro into"
  type        = string
}

variable "token" {
  description = "Ubuntu Pro token to use to attach support subscriptions"
  type        = string
  default     = ""
}
