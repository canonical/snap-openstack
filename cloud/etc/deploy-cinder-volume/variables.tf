# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "charm_cinder_volume_channel" {
  description = "Operator channel for cinder_volume deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_cinder_volume_revision" {
  description = "Operator channel revision for cinder_volume deployment"
  type        = number
  default     = null
}

variable "charm_cinder_volume_config" {
  description = "Operator config for cinder_volume deployment"
  type        = map(string)
  default     = {}
}

variable "cinder_volume_channel" {
  description = "Cinder Volume channel to deploy, not the operator channel"
  default     = "2024.1/edge"
}

variable "charm_cinder_volume_ceph_channel" {
  description = "Operator channel for cinder_volume_ceph deployment"
  type        = string
  default     = "2024.1/edge"
}

variable "charm_cinder_volume_ceph_revision" {
  description = "Operator channel revision for cinder_volume_ceph deployment"
  type        = number
  default     = null
}

variable "charm_cinder_volume_ceph_config" {
  description = "Operator config for cinder_volume_ceph deployment"
  type        = map(string)
  default     = {}
}

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "machine_model" {
  description = "Model to deploy to"
  type        = string
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for cinder_volume"
  type        = set(map(string))
  default     = null
}

variable "cinder_volume_ceph_endpoint_bindings" {
  description = "Endpoint bindings for cinder_volume_ceph"
  type        = set(map(string))
  default     = null
}

variable "keystone-offer-url" {
  description = "Offer URL for openstack keystone endpoints"
  type        = string
  default     = null
}

variable "amqp-offer-url" {
  description = "Offer URL for amqp"
  type        = string
  default     = null
}

variable "database-offer-url" {
  description = "Offer URL for database"
  type        = string
  default     = null
}

# Microceph is hosted in the same model as cinder-volume-ceph
variable "ceph-application-name" {
  description = "Ceph application name"
  type        = string
  default     = null
}
