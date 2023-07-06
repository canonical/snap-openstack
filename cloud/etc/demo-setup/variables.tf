# External networking
variable "external_network" {
  type = object({
    cidr             = string
    gateway          = string
    start            = string
    end              = string
    physical_network = string
    network_type     = string
    segmentation_id  = number
  })
}


# User setup
variable "user" {
  type = object({
    username             = string
    password             = string
    cidr                 = string
    security_group_rules = bool
    dns_nameservers      = list(string)
  })
  sensitive = true
}
