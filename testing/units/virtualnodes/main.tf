terraform {
  required_version = ">= 0.14.0"
  required_providers {
    libvirt = {
      source = "dmacvicar/libvirt"
      version = "0.9.1"
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
  generic_net_addresses   = {
    start   = "172.16.1.2",
    end     = "172.16.1.254",
    cidr    = "172.16.1.0/24",
    gateway = "172.16.1.1"
  }
  external_net_addresses   = {
    start   = "172.16.2.2",
    end     = "172.16.2.254",
    cidr    = "172.16.2.0/24",
    gateway = "172.16.2.1"
  }
}

#### Networks

resource "libvirt_network" "generic_net" {
  name      = "generic_net"
  autostart = true

  domain = {
    name = var.generic_net_domain
  }
  forward = {
    nat = {
      ports = [
        {
          start = 1024
          end   = 65535
        }
      ]
    }
  }
  ips = [
    {
      address = local.generic_net_addresses.gateway
      prefix = 24
    }
  ]
}

resource "libvirt_network" "external_net" {
  name      = "external_net"
  autostart = true

  domain = {
    name = var.external_net_domain
  }
  forward = {
    nat = {
      ports = [
        {
          start = 1024
          end   = 65535
        }
      ]
    }
  }
  ips = [
    {
      address = local.external_net_addresses.gateway
      prefix = 24
    }
  ]
}

resource "libvirt_pool" "sunbeam" {
  name = "sunbeam"
  type = "dir"
  target = {
    path = var.storage_pool_path
  }
}

#### Volumes

resource "libvirt_volume" "node_vol" {
  name      = "node_${count.index}.qcow2"
  count     = var.nodes_count
  pool      = libvirt_pool.sunbeam.name
  capacity  = var.node_rootfs_size
  target = { format = { type = "qcow2" } }
}

resource "libvirt_volume" "node_vol_secondary" {
  name      = "node_${count.index}_secondary.qcow2"
  count     = var.nodes_count
  pool      = libvirt_pool.sunbeam.name
  capacity  = var.node_secondary_disk_size
  target = { format = { type = "qcow2" } }
}


resource "libvirt_volume" "ubuntu_noble" {
  name   = "ubuntu-noble.qcow2"
  pool   = libvirt_pool.sunbeam.name
  target = { format = { type = "qcow2" } }
  create = {
    content = {
      url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    }
  }
}

resource "libvirt_volume" "maas_controller_vol" {
  name      = "maas-controller-vol"
  pool      = libvirt_pool.sunbeam.name
  capacity  = var.maas_controller_rootfs_size
  target = { format = { type = "qcow2" } }
  backing_store = {
    path = libvirt_volume.ubuntu_noble.path
    format = { type = "qcow2" }
  }
}

#### Virtual machines (domains)

resource "libvirt_cloudinit_disk" "maas_controller_cloudinit" {
  name      = "maas_controller_cloudinit.iso"
  meta_data = yamlencode({
    instance-id    = "maas-controller"
    local-hostname = "maas-controller"
  })
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

resource "libvirt_volume" "maas_controller_cloudinit_vol" {
  name   = "maas_controller_cloudinit_vol"
  pool = libvirt_pool.sunbeam.name
  create = {
    content = {
      url = libvirt_cloudinit_disk.maas_controller_cloudinit.path
    }
  }
}

resource "libvirt_domain" "maas_controller" {
  name    = "maas-controller"
  memory  = var.maas_controller_mem
  vcpu    = var.maas_controller_vcpu
  running = true
  type    = "kvm"
  cpu     = { mode = "host-passthrough" }

  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = "q35"
    boot_devices = [{ dev = "hd" }]
  }
  devices = {
    disks = [
      {
        source = {
          volume = {
            pool = libvirt_pool.sunbeam.name
            volume = libvirt_volume.maas_controller_vol.name
          }
        }
        target = { dev = "sda", bus = "virtio" }
        driver = { name = "qemu", type = "qcow2" }
      },
      {
        source = {
          volume = {
            pool = libvirt_pool.sunbeam.name
            volume = libvirt_volume.maas_controller_cloudinit_vol.name
          }
        }
        target = { dev = "hdd", bus = "virtio" }
      }
    ]
    interfaces = [
      {
        model = { type = "virtio"}
        source = {
          network = {
            network = libvirt_network.generic_net.name
          }
        }
        mac = { address = var.maas_controller_mac_address }
      }
    ]
    consoles = [
      { target = { type = "serial" } },
      { target = { type = "virtio", port = "1" }}
    ]
    graphics = [
      {
        type = "vnc"
        vnc = {
          autoport = true
          listen   = "127.0.0.1"
        }
      },
    ]
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
  type   = "kvm"
  cpu = {
    mode = "host-passthrough"
  }
  os = {
    type         = "hvm"
    type_arch    = "x86_64"
    type_machine = "q35"
    boot_devices = [{ dev = "network" }, { dev = "hd" }]
  }
  devices = {
    disks = [
      {
        serial = format("DISK-ROOT-%06d", count.index)
        source = {
          volume = {
            pool = libvirt_pool.sunbeam.name
            volume = libvirt_volume.node_vol[count.index].name
          }
        }
        target = { dev = "sda", bus = "virtio" }
        driver = { name = "qemu", type = "qcow2" }
      },
      {
        serial = format("DISK-CEPH-%06d", count.index)
        source = {
          volume = {
            pool = libvirt_pool.sunbeam.name
            volume = libvirt_volume.node_vol_secondary[count.index].name
          }
        }
        target = { dev = "sdb", bus = "virtio" }
        driver = { name = "qemu", type = "qcow2" }
      }
    ]
    interfaces = [
      {
        model = { type = "virtio"}
        source = {
          network = {
            network = libvirt_network.generic_net.name
          }
        }
        mac = { address = format("52:54:00:11:22:%02d", count.index + 10) }
      },
      {
        model = { type = "virtio"}
        source = {
          network = {
            network = libvirt_network.external_net.name
          }
        }
        mac = { address = format("52:54:00:33:44:%02d", count.index + 10) }
      }
    ]
    consoles = [
      { target = { type = "serial" } },
      { target = { type = "virtio", port = "1" }}
    ]
    graphics = [
      {
        type = "vnc"
        vnc = {
          autoport = true
          listen   = "127.0.0.1"
        }
      },
    ]
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
