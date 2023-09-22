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

variable "charm_microk8s_channel" {
  description = "Operator channel for charm microk8s deployment"
  default     = "1.28/stable"
}

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "enable-addons" {
  description = "Enable microk8s addons"
  default     = false
}

variable "addons-model" {
  description = "Name of Juju model to use for deployment of addons"
  default     = "microk8s-addons"
}

variable "cloud" {
  description = "Name of K8S cloud to use for deployment"
  default     = "microk8s"
}

# https://github.com/juju/terraform-provider-juju/issues/147
variable "credential" {
  description = "Name of credential to use for deployment"
  default     = ""
}

variable "config" {
  description = "Set configuration on model"
  default     = {}
}

variable "charm-metallb-channel" {
  description = "Operator channel for metallb deployment"
  default     = "1.28/stable"
}

variable "charm-coredns-channel" {
  description = "Operator channel for coredns deployment"
  default     = "1.28/stable"
}

variable "coredns-ha-scale" {
  description = "Scale of coredns deployment"
  default     = 1
}

variable "metallb-iprange" {
  description = "IP address range to assign for services"
  default     = "10.20.21.1-10.20.21.10"
}
