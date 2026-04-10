# Terraform manifest for Octavia Amphora OpenStack resource setup
# Creates the required OpenStack resources for the Octavia Amphora provider:
# - Amphora image (downloaded from URL)
# - Amphora flavor
# - lb-mgmt-net network and subnet
# - lb-mgmt-sec-grp security group and rules
# - lb-health-mgr-sec-grp security group and rules
#
# SPDX-FileCopyrightText: 2024 - Canonical Ltd
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
  lb_mgmt_cidr       = var.create-lb-mgmt-network ? var.lb-mgmt-cidr : data.openstack_networking_subnet_v2.lb_mgmt_existing[0].cidr
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
  cidr        = var.lb-mgmt-cidr
  ip_version  = 4
  enable_dhcp = true
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
  ethertype         = "IPv4"
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
  ethertype         = "IPv4"
  protocol          = "icmp"
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.lb_mgmt[0].id
}

resource "openstack_networking_secgroup_rule_v2" "lb_mgmt_22" {
  count             = var.create-lb-secgroups ? 1 : 0
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
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
  ethertype         = "IPv4"
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
  ethertype         = "IPv4"
  # All control-plane traffic from Amphora originates on lb-mgmt-net.
  remote_ip_prefix  = local.lb_mgmt_cidr
  security_group_id = openstack_networking_secgroup_v2.lb_health[0].id
}
