include "stack" {
  path = find_in_parent_folders("stack.hcl")

  # NOTE(freyes): expose shouldn't be needed to access `locals` in `stack.hcl`,
  # although without it `stack` is `null`.
  expose = true
}

terraform {
  source = "./"
}

inputs = include.stack.locals.stack_config
