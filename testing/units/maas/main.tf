locals {
  generic_net_addresses   = ["172.16.1.0/24"]
  external_net_addresses  = ["172.16.2.0/24"]
  generic_dhcp_start = cidrhost(local.generic_net_addresses[0], 200)
  generic_dhcp_end = cidrhost(local.generic_net_addresses[0], 254)
}

resource "maas_configuration" "kernel_opts" {
  key   = "kernel_opts"
  value = "console=ttyS0 console=tty0"
}

resource "maas_configuration" "dnssec_disable" {
  key   = "dnssec_validation"
  value = "no"
}

resource "maas_configuration" "completed_intro" {
  key   = "completed_intro"
  value = "true"
}

resource "maas_configuration" "upstream_dns" {
  key   = "upstream_dns"
  value = var.upstream_dns_server
}

# NOTE(freyes): this selection is made automatically by MAAS when installed,
# running this block raises the following error:
#
# Error: error creating ubuntu noble: ServerError: 400 Bad Request ({"__all__":
# ["Boot source selection with this Boot source, Os and Release already
# exists."]})
#
# It's possible to use the `import` block, although there seems to not be a need
# of making this a managed resource at the moment.
#
# data "maas_boot_source" "boot_source" {}
#
# resource "maas_boot_source_selection" "amd64" {
#   boot_source = data.maas_boot_source.boot_source.id
#   os      = "ubuntu"
#   release = "noble"
#   arches  = ["amd64"]
# }


# Generate SSH key pair
resource "tls_private_key" "ssh_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

# Save private key to local file
resource "local_file" "private_key" {
  content         = tls_private_key.ssh_key.private_key_pem
  filename        = "${path.module}/../../private/id_rsa"
  file_permission = "0600"
}

# Read existing file content
data "local_file" "existing_keys" {
  filename = pathexpand("~/.ssh/authorized_keys")
}

locals {
  existing_content = try(data.local_file.existing_keys.content, "")
  new_key          = tls_private_key.ssh_key.public_key_openssh
  all_keys         = "${local.existing_content}${local.new_key}"

  kvm_host_addr = provider::netparse::parse_url(var.libvirt_uri).host
}

resource "local_file" "updated_keys" {
  content  = local.all_keys
  filename = pathexpand("~/.ssh/authorized_keys")
}

resource "null_resource" "maas_controller_null" {

  connection {
    type        = "ssh"
    user        = "ubuntu"
    private_key = file(var.ssh_private_key_path)
    host        = var.maas_controller_ip_address
  }

  provisioner "remote-exec" {
    inline = [
      "#!/bin/bash",
      "sudo mkdir -p /var/snap/maas/current/root/.ssh",
      "echo '${tls_private_key.ssh_key.private_key_openssh}' | sudo tee /var/snap/maas/current/root/.ssh/id_rsa",
      "sudo chmod 700 /var/snap/maas/current/root/.ssh",
      "sudo chmod 600 /var/snap/maas/current/root/.ssh/id_rsa",
      "ssh-keyscan -H ${local.kvm_host_addr} | sudo tee -a /var/snap/maas/current/root/.ssh/known_hosts",
      "sudo chmod 600 /var/snap/maas/current/root/.ssh/known_hosts",
    ]
  }
}


data "maas_rack_controller" "primary" {
  hostname = var.maas_hostname
}

resource "maas_space" "space_external" {
  name = "space-external"
}

resource "maas_space" "space_generic" {
  name = "space-generic"
}

# Fabric - generic
data "maas_fabric" "generic_fabric" {
  name = "fabric-0"
}

import {
  to = maas_fabric.generic_fabric
  id = "${data.maas_fabric.generic_fabric.id}"
}

resource "maas_fabric" "generic_fabric" {
  name = "fabric-0"
}

# VLAN - Generic
data "maas_vlan" "generic_vlan" {
  fabric = resource.maas_fabric.generic_fabric.id
  vlan   = 0
}

import {
  to = maas_vlan.generic_vlan
  id = "${data.maas_fabric.generic_fabric.name}:0"
}

resource "maas_vlan" "generic_vlan" {
  fabric = maas_fabric.generic_fabric.id
  vid    = 0
  name   = "untagged"
  space  = maas_space.space_generic.name
}

# Subent - generic

data "maas_subnet" "generic_subnet" {
  cidr   = local.generic_net_addresses[0]
}

import {
  to = maas_subnet.generic_subnet
  id = "${data.maas_subnet.generic_subnet.cidr}"
}

resource "maas_subnet" "generic_subnet" {
  name   = local.generic_net_addresses[0]
  cidr   = local.generic_net_addresses[0]
  fabric = maas_fabric.generic_fabric.id
  vlan   = maas_vlan.generic_vlan.vid
}

resource "maas_subnet_ip_range" "generic_subnet_dhcp_range" {
  subnet   = maas_subnet.generic_subnet.id
  start_ip = local.generic_dhcp_start
  end_ip   = local.generic_dhcp_end
  type     = "dynamic"
}

resource "maas_vlan_dhcp" "generic_vlan_dhcp" {
  fabric                  = maas_fabric.generic_fabric.id
  vlan                    = maas_vlan.generic_vlan.vid
  primary_rack_controller = data.maas_rack_controller.primary.id
  ip_ranges               = [maas_subnet_ip_range.generic_subnet_dhcp_range.id]
}

# resource "maas_machine" "node" {
#   count = length(var.nodes)
#   hostname = var.nodes[count.index].name
#   power_type = "virsh"
#   power_parameters = jsonencode({
#     power_address = var.libvirt_uri
#     power_id      = var.nodes[count.index].name
#   })
#   pxe_mac_address = var.nodes[count.index].mac_address
# }
