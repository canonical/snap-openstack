terraform {
  required_version = ">= 0.14.0"
  required_providers {
    libvirt = {
      source = "dmacvicar/libvirt"
      version = "0.8.3"
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
}

#### Networks

resource "libvirt_network" "mgmt_net" {
  name = "mgmt_net"
  mode = var.mgmt_net_mode

  domain = var.mgmt_net_domain
  addresses = var.mgmt_net_addresses

  dns {
    enabled = false
  }
}

resource "libvirt_volume" "node_vol" {
  name  = "node_${count.index}.qcow2"
  count = var.nodes_count
  size  = var.node_rootfs_size
}

resource "libvirt_volume" "node_vol_secondary" {
  name  = "node_${count.index}_secondary.qcow2"
  count = var.nodes_count
  size  = var.node_secondary_disk_size
}


#### Volumes

resource "libvirt_volume" "ubuntu_noble" {
  name   = "ubuntu-noble.qcow2"
  source = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

resource "libvirt_volume" "maas_controller_vol" {
  name           = "maas-controller-vol"
  base_volume_id = libvirt_volume.ubuntu_noble.id
  size           = var.maas_controller_rootfs_size
}

#### Virtual machines (domains)

resource "libvirt_cloudinit_disk" "maas_controller_cloudinit" {
  name      = "maas_controller_cloudinit.iso"
  user_data = templatefile(
    "${path.module}/templates/maas_controller.cloudinit.cfg",
    {
      ssh_public_key = file(var.ssh_public_key_path)
    })
  network_config = templatefile(
    "${path.module}/templates/maas_controller.netplan.yaml",
    {
      mac_address = var.maas_controller_mac_address
      dns_server  = var.upstream_dns_server
    })
}

resource "libvirt_domain" "maas_controller" {
  name = "maas-controller"
  disk {
    volume_id = libvirt_volume.maas_controller_vol.id
    scsi      = "true"
  }
  cloudinit = libvirt_cloudinit_disk.maas_controller_cloudinit.id
  boot_device {
    dev = [ "hd"]
  }
  network_interface {
    network_id     = libvirt_network.mgmt_net.id
    hostname       = "maas_controller"
    addresses      = ["10.17.3.3"]
    mac            = var.maas_controller_mac_address
    wait_for_lease = true
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
    network_id     = libvirt_network.mgmt_net.id
    hostname       = "node-${count.index}"
    mac            = format("AA:BB:CC:11:22:%02d", count.index + 10)
    wait_for_lease = true
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
