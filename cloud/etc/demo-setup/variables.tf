# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# External networking
variable "external_network" {
  type = map(object({
    cidr             = string
    gateway          = string
    range            = string
    network_type     = string
    segmentation_id  = number
  }))
}


# User setup
variable "user" {
  type = object({
    username             = string
    password             = string
    cidr                 = string
    security_group_rules = bool
    dns_nameservers      = list(string)
    physnet              = string
  })
  sensitive = true
}


# Image resource distro, e.g noble etc
variable "distro_version" {
  type    = string
  default = "noble"
}


# Image resource arch, e.g amd64
variable "distro_arch" {
  type    = string
  default = "amd64"
}
