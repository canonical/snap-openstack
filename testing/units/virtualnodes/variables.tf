variable "libvirt_uri" {
  type = string
  default = "qemu:///system"
}

variable "nodes_count" {
  type    = number
  default = 6
}

variable "node_mem" {
  type    = string
  default = "2048"
}

variable "node_vcpu" {
  type    = number
  default = 2
}

variable "maas_controller_mem" {
  type    = string
  default = "4096"  # 4GiB
}

variable "maas_controller_vcpu" {
  type    = number
  default = 2
}

variable "node_rootfs_size" {
  description = "Node rootfs disk size (in bytes)"
  type        = number
  default     = 21474836480  # 20 GiB
}

variable "node_secondary_disk_size" {
  description = "Node secondary disk size (in bytes)"
  type        = number
  default     = 21474836480  # 20 GiB
}

variable "generic_net_domain" {
  description = ""
  type        = string
  default     = "generic.maas"
}

variable "external_net_domain" {
  description = ""
  type        = string
  default     = "external.maas"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key to use in deployed nodes"
  type        = string
  default     = "~/.ssh/id_ecdsa.pub"
}

variable "ssh_private_key_path" {
  description = "Path to the SSH private key to use in deployed nodes"
  type        = string
  default     = "~/.ssh/id_ecdsa"
}

variable "upstream_dns_server" {
  description = "upstream dns server to use in MAAS"
  type        = string
  default     = "8.8.8.8"
}

variable "maas_controller_mac_address" {
  description = "MAC address to assign to the maas controller nic in the management network"
  type        = string
  default     = "AA:BB:CC:11:11:01"
}

variable "maas_controller_rootfs_size" {
  description = "MAAS Controller rootfs disk size (in bytes)"
  type        = number
  default     = 21474836480  # 20 GiB
}
