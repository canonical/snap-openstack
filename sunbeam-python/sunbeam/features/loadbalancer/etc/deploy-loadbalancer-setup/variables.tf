# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# Conditional creation flags — set to false to skip creating a resource
# when the user is providing an existing ID instead.
variable "create-amphora-image" {
  description = "Whether to download and create the Amphora image in Glance"
  type        = bool
  default     = true
}

variable "create-amphora-flavor" {
  description = "Whether to create the Amphora Nova flavor"
  type        = bool
  default     = true
}

variable "create-lb-mgmt-network" {
  description = "Whether to create the lb-mgmt network and subnet"
  type        = bool
  default     = true
}

variable "create-lb-secgroups" {
  description = "Whether to create the lb-mgmt security groups"
  type        = bool
  default     = true
}

variable "amphora-image-name" {
  description = "Name for the Amphora image in Glance"
  type        = string
  default     = "amphora-x64-haproxy"
}

variable "amphora-image-tag" {
  description = "Glance tag applied to the uploaded Amphora image (must match amp_image_tag configured in Octavia)"
  type        = string
  default     = "octavia-amphora"
}

variable "amphora-image-url" {
  description = "URL to download the Amphora image from"
  type        = string
  default     = "https://tarballs.opendev.org/openstack/octavia/test-images/test-only-amphora-x64-haproxy-ubuntu-noble.qcow2"
}

variable "amphora-flavor-name" {
  description = "Name for the Amphora Nova flavor"
  type        = string
  default     = "amphora"
}

variable "amphora-flavor-ram" {
  description = "RAM in MB for Amphora flavor"
  type        = number
  default     = 1024
}

variable "amphora-flavor-vcpus" {
  description = "vCPU count for Amphora flavor"
  type        = number
  default     = 1
}

variable "amphora-flavor-disk" {
  description = "Disk size in GB for Amphora flavor"
  type        = number
  default     = 2
}

variable "lb-mgmt-cidr" {
  description = "CIDR for the lb-mgmt-net subnet (used when create-lb-mgmt-network = true)"
  type        = string
  default     = "172.31.0.0/24"
}

# ---- Existing resource IDs (used when create-* = false) --------------------
# When the user provides an existing resource, pass its ID here so Terraform
# can look it up via a data source.  This keeps outputs always populated and
# allows the security group rules to derive the lb-mgmt CIDR automatically
# from the existing subnet rather than requiring it to be specified manually.

variable "existing-amp-flavor-id" {
  description = "Existing Nova flavor ID for Amphora (set when create-amphora-flavor = false)"
  type        = string
  default     = ""
}

variable "existing-lb-mgmt-network-id" {
  description = "Existing lb-mgmt Neutron network ID (set when create-lb-mgmt-network = false)"
  type        = string
  default     = ""
}

variable "existing-lb-mgmt-subnet-id" {
  description = "Existing lb-mgmt Neutron subnet ID (set when create-lb-mgmt-network = false)"
  type        = string
  default     = ""
}
