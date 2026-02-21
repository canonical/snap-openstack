# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "snap_channel" {
  description = "Snap channel to deploy openstack-hypervisor snap from"
  type        = string
  default     = "2024.1/stable"
}

variable "charm_channel" {
  description = "Charm channel to deploy openstack-hypervisor charm from"
  type        = string
  default     = "2024.1/stable"
}

variable "charm_revision" {
  description = "Charm channel revision to deploy openstack-hypervisor charm from"
  type        = number
  default     = null
}

variable "charm_config" {
  description = "Charm config to deploy openstack-hypervisor charm from"
  type        = map(string)
  default     = {}
}

variable "openstack_model" {
  description = "Name of OpenStack model."
  type        = string
}

variable "machine_model" {
  description = "Name of model to deploy hypervisor into."
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for openstack-hypervisor"
  type        = set(map(string))
  default     = null
}

# Mandatory relation, no defaults
variable "rabbitmq-offer-url" {
  description = "Offer URL for openstack rabbitmq"
  type        = string
}

# Mandatory relation, no defaults
variable "keystone-offer-url" {
  description = "Offer URL for openstack keystone identity-credentials relation"
  type        = string
}

variable "cert-distributor-offer-url" {
  description = "Offer URL for openstack keystone certificate-transfer relation"
  type        = string
  default     = null
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

variable "ceilometer-offer-url" {
  description = "Offer URL for openstack ceilometer"
  type        = string
  default     = null
}

variable "cinder-volume-ceph-application-name" {
  description = "Name for cinder-volume-ceph application"
  type        = string
  default     = null
}

# Mandatory relation, no defaults
variable "nova-offer-url" {
  description = "Offer URL for openstack nova"
  type        = string
}

variable "masakari-offer-url" {
  description = "Offer URL for openstack masakari"
  type        = string
  default     = null
}
