# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

variable "openstack-model-uuid" {
  description = "UUID of the OpenStack Juju model"
  type        = string
}

variable "enable-disaster-recovery" {
  description = "Enable disaster recovery resources"
  type        = bool
  default     = false
}

variable "s3-integrator-channel" {
  description = "Channel to use for deployment of s3-integrator charm"
  type        = string
  default     = "2/stable"
}

variable "s3-integrator-revision" {
  description = "Charm revision for s3-integrator deployment"
  type        = number
  default     = null
}

variable "s3-integrator-config" {
  description = "Operator config for s3-integrator deployment"
  type        = map(map(string))
  default     = {}
}

variable "s3-integrator-secret-data" {
  description = "Per-app secret payload for s3-integrator credentials"
  type        = map(map(string))
  default     = {}
}

variable "s3-integrator-apps" {
  description = "Per-application s3-integrator app names to deploy"
  type        = list(string)
  default     = []
}

variable "s3-integrations" {
  description = "Map of target app -> per-app s3-integrator app for relation wiring"
  type = map(object({
    integrator_app = string
    target_endpoint = string
  }))
  default     = {}
}
