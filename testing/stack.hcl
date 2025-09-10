locals {
  units = {
    virtualnodes = {
      source = "./virtualnodes"
    }

    maas = {
      source = "./maas"
      dependencies = ["virtualnodes"]
    }
  }

  # Stack-wide variables
  stack_config = {
    ssh_private_key_path = "~/.ssh/passwordless"
    ssh_public_key_path = "~/.ssh/passwordless.pub"
    libvirt_uri         = get_env("LIBVIRT_DEFAULT_URI", "qemu:///system")
    maas_hostname       = "maas-controller"
  }
}
