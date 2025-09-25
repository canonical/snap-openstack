variable "libvirt_uri" {
  description = "Libvirt connection URI"
  type        = string
  default     = "qemu:///system"
}

variable "maas_api_key" {
  description = "MAAS Admin API Key"
  type        = string
}

variable "maas_controller_ip_address" {
  description = "MAAS Controller IP Address"
  type        = string
}

variable "maas_hostname" {
  description = "MAAS controller hostname"
  type        = string
}

variable "nodes" {
  description = "List of (virtual) nodes"
  type = list(object({
    name        = string
    mac_address = string
  }))
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
