# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

output "multus-app-name" {
  value = juju_application.multus.name
}

output "openstack-port-cni-app-name" {
  value = juju_application.openstack-port-cni.name
}
