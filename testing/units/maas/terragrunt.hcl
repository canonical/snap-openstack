include "stack" {
  path   = find_in_parent_folders("stack.hcl")
  expose = true
}

terraform {
  source = "."
}

dependency "virtualnodes" {
  config_path = "../virtualnodes"

  mock_outputs = {
    maas_api_key               = "ConsumerSecret:TokenKey:TokenSecret"
    maas_controller_ip_address = "1.2.3.4"
    nodes = []
  }
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
}

# Generate provider configuration using dependency outputs
generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite"
  contents = templatefile("${get_parent_terragrunt_dir()}/templates/maas-provider.tf.tpl", {
    api_key               = dependency.virtualnodes.outputs.maas_api_key
    controller_ip_address = dependency.virtualnodes.outputs.maas_controller_ip_address
  })
}

inputs = merge(
  include.stack.locals.stack_config,
  {
    maas_api_key = dependency.virtualnodes.outputs.maas_api_key
    maas_controller_ip_address = dependency.virtualnodes.outputs.maas_controller_ip_address
    nodes = dependency.virtualnodes.outputs.nodes
    # TODO(freyes): make `172.16.1.1` dynamic, it's the gateway of the generic
    # network created by libvirt, so this should be an output of the
    # virtualnodes' unit.
    libvirt_uri = format("qemu+ssh://%s@%s/system", get_env("USER"), "172.16.1.1")
  }
)
