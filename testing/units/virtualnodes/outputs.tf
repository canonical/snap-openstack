output "maas_controller_ip_address" {
  description = "MAAS Controller IP Address."
  value = local.maas_controller_ip_addr
  depends_on = [libvirt_domain.maas_controller]
}

output "nodes" {
  description = "List of (virtual) nodes"
  value = [
    for node in libvirt_domain.node : {
      name        = node.name
      mac_address = node.network_interface[0].mac
    }
  ]
  depends_on = [libvirt_domain.node]
}

output "maas_api_key" {
  description = "MAAS Admin API Key"
  value = data.external.remote_command.result.apikey
  sensitive = true
  depends_on = [libvirt_domain.maas_controller]
}
