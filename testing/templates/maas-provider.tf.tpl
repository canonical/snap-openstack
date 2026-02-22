terraform {
  required_version = ">= 0.14.0"
  required_providers {
    local = {
      source = "hashicorp/local"
      version = "2.5.3"
    }
    maas = {
      source = "canonical/maas"
      version = "2.6.0"
    }
    netparse = {
      source = "gmeligio/netparse"
      version = "0.0.4"
    }
    null = {
      source = "hashicorp/null"
      version = "3.2.4"
    }
    tls = {
      source = "hashicorp/tls"
      version = "4.1.0"
    }
  }
}

provider "local" {
}

provider "maas" {
  api_version = "2.0"
  api_key     = "${api_key}"
  api_url     = "http://${controller_ip_address}:5240/MAAS"
}

provider "null" {
}

provider "netparse" {
}

provider "tls" {
}
