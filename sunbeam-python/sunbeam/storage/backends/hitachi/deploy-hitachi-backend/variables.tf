# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "machine_model" {
  description = "Name of the machine model to deploy to"
  type        = string
}

variable "charm_hitachi_channel" {
  description = "Operator channel for Hitachi backend deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_hitachi_revision" {
  description = "Operator channel revision for Hitachi backend deployment"
  type        = number
  default     = null
}

variable "hitachi_backends" {
  description = "Map of Hitachi backend configurations"
  type = map(object({
    charm_config = map(string)
    
    # Main array credentials (required)
    san_username = string
    san_password = string
    
    # CHAP credentials (optional)
    use_chap_auth = optional(bool, false)
    chap_username = optional(string, "")
    chap_password = optional(string, "")
    
    # Mirror CHAP credentials (optional)
    hitachi_mirror_chap_username = optional(string, "")
    hitachi_mirror_chap_password = optional(string, "")
    
    # Mirror REST API credentials (optional)
    hitachi_mirror_rest_username = optional(string, "")
    hitachi_mirror_rest_password = optional(string, "")
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
