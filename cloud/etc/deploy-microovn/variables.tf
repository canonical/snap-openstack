# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm_microovn_channel" {
  type    = string
  default = "latest/edge"
}

variable "charm_microovn_revision" {
  description = "Operator channel revision for microovn deployment"
  type        = number
  default     = null
}

variable "charm_microovn_config" {
  description = "Operator config for microovn deployment"
  type        = map(string)
  default     = {}
}

variable "charm_openstack_network_agents_channel" {
  description = "Operator channel for openstack-network-agents deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_openstack_network_agents_revision" {
  description = "Operator channel revision for openstack-network-agents deployment"
  type        = number
  default     = null
}

variable "charm_openstack_network_agents_config" {
  description = "Operator config for openstack-network-agents deployment"
  type        = map(string)
  default     = {}
}

variable "charm_microcluster_token_distributor_channel" {
  description = "Operator channel for microcluster-token-distributor deployment"
  type        = string
  default     = "latest/edge"
}

variable "charm_microcluster_token_distributor_revision" {
  description = "Operator channel revision for microcluster-token-distributor deployment"
  type        = number
  default     = null
}

variable "charm_microcluster_token_distributor_config" {
  description = "Operator config for microcluster-token-distributor deployment"
  type        = map(string)
  default     = {}
}

variable "charm_sunbeam_ovn_proxy_channel" {
  description = "Operator channel for sunbeam-ovn-proxy deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_sunbeam_ovn_proxy_revision" {
  description = "Operator channel revision for sunbeam-ovn-proxy deployment"
  type        = number
  default     = null
}

variable "charm_sunbeam_ovn_proxy_config" {
  description = "Operator config for sunbeam-ovn-proxy deployment"
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
