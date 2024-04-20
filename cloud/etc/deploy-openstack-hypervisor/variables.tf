# Copyright (c) 2023 Canonical Ltd.
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

variable "openstack-state-backend" {
  description = "backend type used for openstack state"
  type        = string
  default     = "local"
}
variable "openstack-state-config" {
  type = map(any)
}
