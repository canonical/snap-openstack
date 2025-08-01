# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

# Test variables for Dell Storage Center backend deployment

machine_model = "openstack"

charm_dellsc_name     = "cinder-volume-dellsc"
charm_dellsc_base     = "ubuntu@24.04"
charm_dellsc_channel  = "latest/edge"
charm_dellsc_endpoint = "cinder-volume"
charm_dellsc_revision = 1

dellsc_backends = {
  "dellsc-test" = {
    charm_config = {
      san-ip                    = "192.168.1.100"
      dell-sc-ssn              = "64702"
      protocol                 = "fc"
      dell-sc-api-port         = "3033"
      dell-sc-server-folder    = "openstack"
      dell-sc-volume-folder    = "openstack"
      dell-server-os           = "Red Hat Linux 6.x"
      dell-sc-verify-cert      = "false"
      san-thin-provision       = "true"
      dell-api-async-rest-timeout = "15"
      dell-api-sync-rest-timeout  = "30"
      ssh-conn-timeout         = "30"
      ssh-max-pool-conn        = "5"
      ssh-min-pool-conn        = "1"
    }
    
    # Main array credentials
    san_username = "admin"
    san_password = "password123"
    
    # Secondary array credentials (optional, for dual DSM)
    secondary_san_username = ""
    secondary_san_password = ""
  }
}
