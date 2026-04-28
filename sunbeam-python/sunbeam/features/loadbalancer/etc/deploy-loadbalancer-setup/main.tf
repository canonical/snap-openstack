# Terraform manifest for Octavia Amphora OpenStack resource setup
# Creates the required OpenStack resources for the Octavia Amphora provider:
# - Amphora image (downloaded from URL)
# - Amphora flavor
# - lb-mgmt-net network and subnet
# - lb-mgmt-sec-grp security group and rules
# - lb-health-mgr-sec-grp security group and rules
#
# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_version = ">= 1.5.7"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 3.0.0"
    }
  }
}

provider "openstack" {}

# Get the services project reference
data "openstack_identity_project_v3" "services" {
  name = "services"
}

# ---------------------------------------------------------------------------
# Data source lookups for user-provided resources
# Active only when the corresponding create-* flag is false.
# Allows outputs to always return real IDs and the subnet CIDR to be
# derived automatically for security group rules.
# ---------------------------------------------------------------------------

data "openstack_compute_flavor_v2" "amphora_existing" {
  count     = var.create-amphora-flavor ? 0 : 1
  flavor_id = var.existing-amp-flavor-id
}

data "openstack_networking_network_v2" "lb_mgmt_existing" {
  count      = var.create-lb-mgmt-network ? 0 : 1
  network_id = var.existing-lb-mgmt-network-id
}

data "openstack_networking_subnet_v2" "lb_mgmt_existing" {
  count     = var.create-lb-mgmt-network ? 0 : 1
  subnet_id = var.existing-lb-mgmt-subnet-id
}

# ---------------------------------------------------------------------------
# Locals — unified resource IDs and CIDR regardless of created vs. looked-up
# ---------------------------------------------------------------------------

locals {
  amp_flavor_id      = var.create-amphora-flavor ? openstack_compute_flavor_v2.amphora[0].id : data.openstack_compute_flavor_v2.amphora_existing[0].id
  lb_mgmt_network_id = var.create-lb-mgmt-network ? openstack_networking_network_v2.lb_mgmt[0].id : data.openstack_networking_network_v2.lb_mgmt_existing[0].id
  lb_mgmt_subnet_id  = var.create-lb-mgmt-network ? openstack_networking_subnet_v2.lb_mgmt[0].id : data.openstack_networking_subnet_v2.lb_mgmt_existing[0].id
  # CIDR sourced from the actual subnet so security group rules are always
  # correctly scoped — no manual lb-mgmt-cidr input needed from the user.
  # IPv6 ULA prefix — hardcoded so no user input is required for auto-create.
  lb_mgmt_cidr       = var.create-lb-mgmt-network ? "fd00:a9fe:a9fe::/64" : data.openstack_networking_subnet_v2.lb_mgmt_existing[0].cidr
  # IPv4 alternative (uncomment and comment out above to revert to IPv4):
  # lb_mgmt_cidr     = var.create-lb-mgmt-network ? var.lb-mgmt-cidr : data.openstack_networking_subnet_v2.lb_mgmt_existing[0].cidr

  # Derived from the actual CIDR so secgroup rules match the subnet's IP
  # version regardless of whether the network was auto-created (IPv6) or
  # user-provided (may be IPv4).  Presence of ":" distinguishes IPv6 from IPv4.
  lb_mgmt_ip_version = can(regex(":", local.lb_mgmt_cidr)) ? "IPv6" : "IPv4"
  lb_mgmt_icmp_proto = can(regex(":", local.lb_mgmt_cidr)) ? "ipv6-icmp" : "icmp"
}

# Amphora image - downloaded from upstream URL and uploaded to Glance
# Skipped when create-amphora-image = false (user provides an existing image).
resource "openstack_images_image_v2" "amphora" {
  count            = var.create-amphora-image ? 1 : 0
  name             = var.amphora-image-name
  image_source_url = var.amphora-image-url
  container_format = "bare"
  disk_format      = "qcow2"
  visibility       = "public"
  tags             = [var.amphora-image-tag]
}

# Amphora Nova flavor
# Skipped when create-amphora-flavor = false (user provides an existing flavor ID).
resource "openstack_compute_flavor_v2" "amphora" {
  count     = var.create-amphora-flavor ? 1 : 0
  name      = var.amphora-flavor-name
  ram       = var.amphora-flavor-ram
  vcpus     = var.amphora-flavor-vcpus
  disk      = var.amphora-flavor-disk
  is_public = true
}

# lb-mgmt-net: management network for communication between
# Octavia health manager and Amphora instances.
# Skipped when create-lb-mgmt-network = false (user provides existing network+subnet IDs).
resource "openstack_networking_network_v2" "lb_mgmt" {
  count          = var.create-lb-mgmt-network ? 1 : 0
  name           = "lb-mgmt-net"
  admin_state_up = true
  tenant_id      = data.openstack_identity_project_v3.services.id
}

resource "openstack_networking_subnet_v2" "lb_mgmt" {
  count       = var.create-lb-mgmt-network ? 1 : 0
  name        = "lb-mgmt-subnet"
  network_id  = openstack_networking_network_v2.lb_mgmt[0].id
  tenant_id   = data.openstack_identity_project_v3.services.id
  # IPv6 ULA subnet with SLAAC — Amphora instances auto-configure their
  # management interface without DHCP, matching the charm-octavia approach.
  cidr               = "fd00:a9fe:a9fe::/64"
  ip_version         = 6
  ipv6_address_mode  = "slaac"
  ipv6_ra_mode       = "slaac"
  enable_dhcp        = true
  # IPv4 alternative (uncomment and comment out above to revert to IPv4):
  # cidr        = var.lb-mgmt-cidr
  # ip_version  = 4
  # enable_dhcp = true
}

# lb-mgmt router — required for IPv6 SLAAC to work.
# With ipv6_ra_mode = "slaac" the Neutron L3 agent sends Router Advertisement
# packets via the router's interface on the subnet.  Without a router attached
# Amphora VMs never receive RA packets and cannot auto-configure their
# management interface addresses.
resource "openstack_networking_router_v2" "lb_mgmt" {
  count          = var.create-lb-mgmt-network ? 1 : 0
  name           = "lb-mgmt-router"
  admin_state_up = true
  tenant_id      = data.openstack_identity_project_v3.services.id
}

resource "openstack_networking_router_interface_v2" "lb_mgmt" {
  count     = var.create-lb-mgmt-network ? 1 : 0
  router_id = openstack_networking_router_v2.lb_mgmt[0].id
  subnet_id = openstack_networking_subnet_v2.lb_mgmt[0].id
}

# lb-mgmt-sec-grp: security group for Amphora instances.
# Skipped when create-lb-secgroups = false (user provides existing secgroup IDs).
resource "openstack_networking_secgroup_v2" "lb_mgmt" {
  count       = var.create-lb-secgroups ? 1 : 0
  name        = "lb-mgmt-sec-grp"
  description = "Security group for Octavia Amphora management"
  tenant_id   = data.openstack_identity_project_v3.services.id
  tags        = ["octavia-amphora"]
}

resource "openstack_networking_secgroup_rule_v2" "lb_mgmt_9443" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = local.lb_mgmt_ip_version
  protocol          = "tcp"
  port_range_min    = 9443
  port_range_max    = 9443
  # Source is always the Octavia health manager, which is on lb-mgmt-net.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_mgmt[0].id
}

resource "openstack_networking_secgroup_rule_v2" "lb_mgmt_icmp" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = local.lb_mgmt_ip_version
  protocol          = local.lb_mgmt_icmp_proto
  # Scoped to lb-mgmt-net so only traffic from within the management
  # network (health manager and other Amphora VMs) can reach Amphora.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_mgmt[0].id
}

resource "openstack_networking_secgroup_rule_v2" "lb_mgmt_22" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = local.lb_mgmt_ip_version
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  # Scoped to lb-mgmt-net so SSH access is limited to the management network.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_mgmt[0].id
}

# lb-health-mgr-sec-grp: security group for Octavia health manager
resource "openstack_networking_secgroup_v2" "lb_health" {
  count       = var.create-lb-secgroups ? 1 : 0
  name        = "lb-health-mgr-sec-grp"
  description = "Security group for Octavia health manager"
  tenant_id   = data.openstack_identity_project_v3.services.id
  tags        = ["octavia-amphora-health"]
}

resource "openstack_networking_secgroup_rule_v2" "lb_health_5555" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = local.lb_mgmt_ip_version
  protocol          = "udp"
  port_range_min    = 5555
  port_range_max    = 5555
  # Source is always Amphora VMs, which are on lb-mgmt-net.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_health[0].id
}

resource "openstack_networking_secgroup_rule_v2" "lb_health_all" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = local.lb_mgmt_ip_version
  # No protocol restriction is intentional: Octavia uses multiple internal
  # protocols between Amphora and the health manager (UDP 5555 heartbeats,
  # TCP for log offloading, stats, etc.).  All of this traffic originates
  # from Amphora VMs on lb-mgmt-net, so scoping to lb_mgmt_cidr is the
  # security boundary — not restricting by protocol.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_health[0].id
}
