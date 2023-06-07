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
  description = "Operator channel for microk8s deployment"
  default     = "legacy/stable"
}

variable "microk8s_channel" {
  description = "K8S channel to deploy, not the operator channel"
  default     = "1.26-strict/stable"
}

variable "machine_ids" {
  description = "List of machine ids to include"
  type        = list(string)
  default     = []
}

variable "addons" {
  description = "Addon configuration to enable on the deployment"
  type        = map(string)
  default = {
    dns              = ""
    hostpath-storage = ""
    metallb          = "10.20.21.1-10.20.21.10"
  }
}

variable "containerd_env" {
  description = "Containerd env file content"
  type        = string
  default     = <<EOT
# This file is managed by Juju. Manual changes may be lost at any time.

# Configure limits for locked memory and maximum number of open files
ulimit -n 65536 || true
ulimit -l 16384 || true

# Uncomment to configure a proxy for containerd
# HTTP_PROXY=http://squid.internal:3128
# HTTPS_PROXY=http://squid.internal:3128
# NO_PROXY=10.0.0.0/8,127.0.0.0/16,192.168.0.0/16
EOT
}
