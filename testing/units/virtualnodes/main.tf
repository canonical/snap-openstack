terraform {
  required_version = ">= 0.14.0"
  required_providers {
    libvirt = {
      source = "dmacvicar/libvirt"
      version = "0.8.3"
    }
    external = {
      source = "hashicorp/external"
      version = "2.3.5"
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

#### Locals
locals {
  maas_controller_ip_addr = "172.16.1.2"
  generic_net_addresses   = ["172.16.1.0/24"]
  external_net_addresses  = ["172.16.2.0/24"]
}

#### Networks

resource "libvirt_network" "generic_net" {
  name = "generic_net"
  mode = "nat"
  autostart = true

  domain = var.generic_net_domain
  addresses = local.generic_net_addresses

  dns {
    enabled = false
  }
}

resource "libvirt_network" "external_net" {
  name = "external_net"
  mode = "nat"
  autostart = true

  domain = var.external_net_domain
  addresses = local.external_net_addresses

  dns {
    enabled = false
  }
}

resource "libvirt_pool" "sunbeam" {
  name = "sunbeam"
  type = "dir"
  target {
    path = var.storage_pool_path
  }
}

#### Volumes

resource "libvirt_volume" "node_vol" {
  name  = "node_${count.index}.qcow2"
  count = var.nodes_count
  pool = libvirt_pool.sunbeam.name
  size  = var.node_rootfs_size
}

resource "libvirt_volume" "node_vol_secondary" {
  name  = "node_${count.index}_secondary.qcow2"
  count = var.nodes_count
  pool = libvirt_pool.sunbeam.name
  size  = var.node_secondary_disk_size
}


resource "libvirt_volume" "ubuntu_noble" {
  name   = "ubuntu-noble.qcow2"
  source = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
  pool = libvirt_pool.sunbeam.name
}

resource "libvirt_volume" "maas_controller_vol" {
  name           = "maas-controller-vol"
  base_volume_id = libvirt_volume.ubuntu_noble.id
  pool = libvirt_pool.sunbeam.name
  size           = var.maas_controller_rootfs_size
}

#### Virtual machines (domains)

resource "libvirt_cloudinit_disk" "maas_controller_cloudinit" {
  name      = "maas_controller_cloudinit.iso"
  pool = libvirt_pool.sunbeam.name
  user_data = templatefile(
    "${path.module}/templates/maas_controller.cloudinit.cfg",
    {
      address        = local.maas_controller_ip_addr
      dns_server     = var.upstream_dns_server
      maas_hostname  = var.maas_hostname
      networks       = "generic:172.16.1.0/24"
      ssh_public_key = file(var.ssh_public_key_path)
    })
  network_config = templatefile(
    "${path.module}/templates/maas_controller.netplan.yaml",
    {
      dns_server  = var.upstream_dns_server
      ip_address  = local.maas_controller_ip_addr
      mac_address = var.maas_controller_mac_address
    })
}

resource "libvirt_domain" "maas_controller" {
  name = "maas-controller"
  memory  = var.maas_controller_mem
  vcpu    = var.maas_controller_vcpu
  disk {
    volume_id = libvirt_volume.maas_controller_vol.id
    scsi      = "true"
  }
  cloudinit = libvirt_cloudinit_disk.maas_controller_cloudinit.id
  boot_device {
    dev = [ "hd"]
  }
  network_interface {
    network_id     = libvirt_network.generic_net.id
    hostname       = var.maas_hostname
    addresses      = [local.maas_controller_ip_addr]
    mac            = var.maas_controller_mac_address
  }
  console {
    type        = "pty"
    target_type = "serial"
    target_port = "0"
  }

  console {
    type        = "pty"
    target_type = "virtio"
    target_port = "1"
  }
  graphics {
    type        = "spice"
    listen_type = "address"
    autoport    = true
  }

  connection {
    type        = "ssh"
    user        = "ubuntu"
    private_key = file(var.ssh_private_key_path)
    host        = local.maas_controller_ip_addr
  }

  provisioner "remote-exec" {
    inline = [
      "until test -f /tmp/.i_am_done; do sleep 10;done",
    ]
  }
}

resource "libvirt_domain" "node" {
  depends_on = [
    libvirt_domain.maas_controller,
  ]
  count   = var.nodes_count
  name    = "node-${count.index}"
  memory  = var.node_mem
  vcpu    = var.node_vcpu
  running = false
  disk {
    volume_id = libvirt_volume.node_vol[count.index].id
    scsi      = "true"
  }
  disk {
    volume_id = libvirt_volume.node_vol_secondary[count.index].id
    scsi      = "true"
  }
  boot_device {
    dev = [ "network"]
  }
  network_interface {
    network_id     = libvirt_network.generic_net.id
    hostname       = "node-${count.index}"
    mac            = format("AA:BB:CC:11:22:%02d", count.index + 10)
  }
  network_interface {
    network_id     = libvirt_network.external_net.id
    mac            = format("AA:BB:CC:33:44:%02d", count.index + 10)
  }
  console {
    type        = "pty"
    target_type = "serial"
    target_port = "0"
  }

  console {
    type        = "pty"
    target_type = "virtio"
    target_port = "1"
  }
  graphics {
    type        = "spice"
    listen_type = "address"
    autoport    = true
  }
}

data "external" "remote_command" {
  depends_on = [
    libvirt_domain.maas_controller
  ]
  program = ["bash", "-c", <<-EOF
    # Block until the api.key file shows up
    API_KEY_FILE=/tmp/maas-api.key
    ssh -i ${var.ssh_private_key_path} ubuntu@${local.maas_controller_ip_addr} 'touch /home/ubuntu/api.key; until [ -s /home/ubuntu/api.key ]; do sleep 5;done; cat /home/ubuntu/api.key' > $API_KEY_FILE
    cat $API_KEY_FILE  2>&1 | jq -R '{apikey: .}'  2>&1
  EOF
  ]
}
