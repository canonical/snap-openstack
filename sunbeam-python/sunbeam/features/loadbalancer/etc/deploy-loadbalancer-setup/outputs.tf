# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# Outputs always return real resource IDs — for user-provided resources the
# value comes from a data source lookup, not from a created resource.

output "amphora-flavor-id" {
  description = "Nova flavor ID for Amphora instances"
  value       = local.amp_flavor_id
}

output "lb-mgmt-secgroup-id" {
  description = "Neutron security group ID for Amphora management (empty if not created)"
  value       = var.create-lb-secgroups ? openstack_networking_secgroup_v2.lb_mgmt[0].id : ""
}

output "lb-health-secgroup-id" {
  description = "Neutron security group ID for health manager (empty if not created)"
  value       = var.create-lb-secgroups ? openstack_networking_secgroup_v2.lb_health[0].id : ""
}

output "lb-mgmt-network-id" {
  description = "Neutron network ID for Amphora management network"
  value       = local.lb_mgmt_network_id
}

output "lb-mgmt-subnet-id" {
  description = "Neutron subnet ID for Amphora management subnet"
  value       = local.lb_mgmt_subnet_id
}
