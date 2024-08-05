# Terraform manifest for deployment of Grafana Agent
#
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

variable "principal-application" {
  description = "Name of the deployed principal application that integrates with grafana-agent"
}

variable "principal-application-model" {
  description = "Name of the model principal application is deployed in"
  default     = "controller"
}

variable "grafana-agent-channel" {
  description = "Channel to use when deploying grafana agent machine charm"
  type        = string
  # Note: Currently, latest/stable is not available for grafana-agent. So,
  # defaulting to latest/candidate.
  default     = "latest/candidate"
}

variable "grafana-agent-revision" {
  description = "Channel revision to use when deploying grafana agent machine charm"
  type        = number
  default     = null
}

variable "grafana-agent-config" {
  description = "Config to use when deploying grafana agent machine charm"
  type        = map(string)
  default     = {}
}

variable "receive-remote-write-offer-url" {
  description = "Offer URL from prometheus-k8s:receive-remote-write application"
  type        = string
  default     = null
}

variable "grafana-dashboard-offer-url" {
  description = "Offer URL from grafana-k8s:grafana-dashboard application"
  type        = string
  default     = null
}

variable "logging-offer-url" {
  description = "Offer URL from loki-k8s:logging application"
  type        = string
  default     = null
}
