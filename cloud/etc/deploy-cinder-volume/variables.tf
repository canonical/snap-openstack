# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
  description = "Offer URL for openstack keystone credentials"
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
