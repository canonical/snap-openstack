# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm_microovn_channel" {
  type    = string
  default = "25.03/stable"
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
  default     = null
}

variable "charm_openstack_network_agents_revision" {
  description = "Operator channel revision for openstack-network-agents deployment"
  type        = number
  default     = null
}

variable "charm_openstack_network_agents_arm64_revision" {
  description = "Operator channel revision for arm64 openstack-network-agents deployment"
  type        = number
  default     = null
}

variable "charm_openstack_network_agents_config" {
  description = "Operator config for openstack-network-agents deployment"
  type        = map(string)
  default     = {}
}

variable "openstack_network_agents_endpoint_bindings" {
  description = "Endpoint bindings for openstack-network-agents (spaces)"
  type = list(object({
    endpoint = optional(string)
    space    = string
  }))
  default = null
}

variable "charm_microcluster_token_distributor_channel" {
  description = "Operator channel for microcluster-token-distributor deployment"
  type        = string
  default     = "v1/stable"
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
  default     = "2026.1/stable"
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
  description = "List of amd64 machine ids to include"
  type        = list(string)
  default     = []
}

variable "microovn_arm64_machine_ids" {
  description = "List of arm64 machine ids to include (e.g. DPU network nodes)"
  type        = list(string)
  default     = []
}

variable "microovn_machine_ids_by_architecture" {
  description = "MicroOVN machine ids grouped by architecture"
  type        = map(list(string))
  default     = {}
}

variable "token_distributor_machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "role_distributor_application_name" {
  description = "Role distributor application name"
  type        = string
  default     = null
}

variable "machine_model_uuid" {
  description = "UUID of Juju model to use for deployment"
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
