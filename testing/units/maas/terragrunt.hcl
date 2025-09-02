include "stack" {
  path   = find_in_parent_folders("stack.hcl")
  expose = true
}

terraform {
  source = "."
}

dependency "vpc" {
  config_path = "../virtualnodes"
}

inputs = merge(
  include.stack.locals.stack_config,
  {
    maas_controller_ip_address = dependency.virtualnodes.outputs.maas_controller_ip_address
    nodes = dependency.virtualnodes.outputs.nodes
  }
)
