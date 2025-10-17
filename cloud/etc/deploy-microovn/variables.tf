# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm_microovn_channel" {
  type    = string
  default = "latest/edge"
}

variable "microovn_snap_channel" {
  description = "MicroOVN snap channel to install via the charm"
  type        = string
  default     = "latest/edge"
}

variable "charm_openstack_network_agents_channel" {
  description = "Operator channel for openstack-network-agents deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_microcluster_token_distributor_channel" {
  description = "Operator channel for microcluster-token-distributor deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_microovn_config" {
  description = "Operator config for microovn deployment"
  type        = map(string)
  default     = {}
}

variable "microovn_machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "token_distributor_machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "machine_model" {
  description = "Model to deploy to"
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for microovn (spaces)"
  type = list(object({
    endpoint = optional(string)
    space    = string
  }))
  default = null
}

variable "ca-offer-url" {
  description = "Offer URL for Certificates"
  type        = string
  default     = null
}

# Mandatory relation, no defaults
variable "ovn-relay-offer-url" {
  description = "Offer URL for ovn relay service"
  type        = string
  default     = null
}
