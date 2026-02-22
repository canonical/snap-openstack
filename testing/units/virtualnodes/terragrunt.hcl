include "stack" {
  path = find_in_parent_folders("stack.hcl")

  # NOTE(freyes): expose shouldn't be needed to access `locals` in `stack.hcl`,
  # although without it `stack` is `null`.
  expose = true
}

terraform {
  source = "./"
  before_hook "create_directories" {
    commands = ["apply", "plan"]
    execute  = ["bash", "-c", <<-EOT
      chmod 755 $HOME
      mkdir -p $HOME/sunbeam_storage
      chmod 775 $HOME/sunbeam_storage
      chgrp libvirt $HOME/sunbeam_storage
      echo "Directory $HOME/sunbeam_storage created"
      ls -ld $HOME/sunbeam_storage
    EOT
    ]
  }
}

inputs = merge(
  include.stack.locals.stack_config,
  {
    storage_pool_path = format("%s/sunbeam_storage", get_env("HOME"))
  }
)
