terraform {
  required_version = ">= 0.14.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "~> 1.48.0"
    }
  }
}

provider "openstack" {}

# Flavors
resource "openstack_compute_flavor_v2" "m1_tiny" {
  name      = "m1.tiny"
  ram       = "512"
  vcpus     = "1"
  disk      = "4"
  is_public = true
}

resource "openstack_compute_flavor_v2" "m1_small" {
  name      = "m1.small"
  ram       = "2048"
  vcpus     = "1"
  disk      = "30"
  is_public = true
}

resource "openstack_compute_flavor_v2" "m1_medium" {
  name      = "m1.medium"
  ram       = "4096"
  vcpus     = "2"
  disk      = "60"
  is_public = true
}

resource "openstack_compute_flavor_v2" "m1_large" {
  name      = "m1.large"
  ram       = "8192"
  vcpus     = "4"
  disk      = "90"
  is_public = true
}

resource "openstack_images_image_v2" "ubuntu" {
  name             = "ubuntu"
  image_source_url = "http://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
  container_format = "bare"
  disk_format      = "qcow2"
  visibility       = "public"

  properties = {
    architecture    = "x86_64"
    hypervisor_type = "qemu"
  }
}

# External networking
resource "openstack_networking_network_v2" "external_network" {
  name           = "external-network"
  admin_state_up = true
  external       = true
  segments {
    physical_network = var.external_network.physical_network
    network_type     = var.external_network.network_type
    segmentation_id  = var.external_network.segmentation_id
  }
}

resource "openstack_networking_subnet_v2" "external_subnet" {
  name        = "external-subnet"
  network_id  = openstack_networking_network_v2.external_network.id
  cidr        = var.external_network.cidr
  ip_version  = 4
  enable_dhcp = false
  allocation_pool {
    start = var.external_network.start
    end   = var.external_network.end
  }
  gateway_ip = var.external_network.gateway
}

# User configuration
resource "openstack_identity_project_v3" "users_domain" {
  name      = "users"
  is_domain = true
}

resource "openstack_identity_project_v3" "user_project" {
  name      = var.user.username
  domain_id = openstack_identity_project_v3.users_domain.id
}

resource "openstack_identity_user_v3" "user" {
  default_project_id = openstack_identity_project_v3.user_project.id
  name               = var.user.username
  password           = var.user.password
  description        = format("Cloud User - %s", var.user.username)
  domain_id          = openstack_identity_project_v3.users_domain.id
}

# Map existing member role into configuration
data "openstack_identity_role_v3" "member" {
  name = "member"
}

resource "openstack_identity_role_assignment_v3" "role_assignment_1" {
  user_id    = openstack_identity_user_v3.user.id
  project_id = openstack_identity_project_v3.user_project.id
  role_id    = data.openstack_identity_role_v3.member.id
}

# User networking
resource "openstack_networking_network_v2" "user_network" {
  name           = format("%s-network", var.user.username)
  admin_state_up = true
  tenant_id      = openstack_identity_project_v3.user_project.id
}

resource "openstack_networking_subnet_v2" "user_subnet" {
  name            = format("%s-subnet", var.user.username)
  network_id      = openstack_networking_network_v2.user_network.id
  tenant_id       = openstack_identity_project_v3.user_project.id
  cidr            = var.user.cidr
  dns_nameservers = var.user.dns_nameservers
}

resource "openstack_networking_router_v2" "user_router" {
  name                = format("%s-router", var.user.username)
  tenant_id           = openstack_identity_project_v3.user_project.id
  admin_state_up      = true
  external_network_id = openstack_networking_network_v2.external_network.id
}

resource "openstack_networking_router_interface_v2" "user_router_interface" {
  router_id = openstack_networking_router_v2.user_router.id
  subnet_id = openstack_networking_subnet_v2.user_subnet.id
}

# Compute quota - set unlimited as this is a sandbox deployment
resource "openstack_compute_quotaset_v2" "compute_quota" {
  project_id           = openstack_identity_project_v3.user_project.id
  key_pairs            = -1
  ram                  = -1
  cores                = -1
  instances            = -1
  server_groups        = -1
  server_group_members = -1
}

# Network quota - set unlimited as this is a sandbox deployment
resource "openstack_networking_quota_v2" "network_quota" {
  project_id          = openstack_identity_project_v3.user_project.id
  floatingip          = -1
  network             = -1
  port                = -1
  rbac_policy         = -1
  router              = -1
  security_group      = -1
  security_group_rule = -1
  subnet              = -1
  subnetpool          = -1
}


data "openstack_networking_secgroup_v2" "secgroup_default" {
  name      = "default"
  tenant_id = openstack_identity_project_v3.user_project.id
}

resource "openstack_networking_secgroup_rule_v2" "secgroup_rule_ssh_ingress" {
  count             = var.user.security_group_rules ? 1 : 0
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = data.openstack_networking_secgroup_v2.secgroup_default.id
  tenant_id         = openstack_identity_project_v3.user_project.id
}

resource "openstack_networking_secgroup_rule_v2" "secgroup_rule_ping_ingress" {
  count             = var.user.security_group_rules ? 1 : 0
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "icmp"
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = data.openstack_networking_secgroup_v2.secgroup_default.id
  tenant_id         = openstack_identity_project_v3.user_project.id
}
