# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "model" {
  description = "OpenStack model name"
  type        = string
  default     = "openstack"
}

variable "multus-channel" {
  description = "Operator channel for multus deployment"
  type        = string
  default     = "latest/stable"
}

variable "multus-revision" {
  description = "Operator channel revision for multus deployment"
  type        = number
  default     = null
}

variable "multus-config" {
  description = "Operator config for multus deployment"
  type        = map(string)
  default     = {}
}

variable "multus-network-attachment-definitions" {
  description = "YAML definitions of NetworkAttachmentDefinitions to create in multus"
  type        = string
  default     = ""
}

variable "openstack-port-cni-channel" {
  description = "Operator channel for openstack-port-cni-k8s deployment"
  type        = string
  default     = "2025.1/edge"
}

variable "openstack-port-cni-revision" {
  description = "Operator channel revision for openstack-port-cni-k8s deployment"
  type        = number
  default     = null
}

variable "openstack-port-cni-config" {
  description = "Operator config for openstack-port-cni-k8s deployment"
  type        = map(string)
  default     = {}
}
