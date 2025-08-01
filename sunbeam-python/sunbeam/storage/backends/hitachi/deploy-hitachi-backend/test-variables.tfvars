machine_model = "openstack"
charm_hitachi_channel = "latest/edge"
charm_hitachi_revision = null

hitachi_backends = {
  "hitachi-backend-1" = {
    charm_config = {
      "hitachi-storage-id" = "123456"
      "hitachi-pools" = "pool1,pool2"
      "san-ip" = "192.168.1.100"
      "protocol" = "FC"
      "volume-backend-name" = "hitachi-backend-1"
    }
    
    # Main array credentials (always required)
    san_username = "maintenance"
    san_password = "secret123"
    
    # CHAP credentials (optional - using iSCSI example)
    use_chap_auth = true
    chap_username = "iscsi_user"
    chap_password = "iscsi_pass"
    
    # Mirror CHAP credentials (optional)
    hitachi_mirror_chap_username = "mirror_chap_user"
    hitachi_mirror_chap_password = "mirror_chap_pass"
    
    # Mirror REST API credentials (optional)
    hitachi_mirror_rest_username = "mirror_rest_user"
    hitachi_mirror_rest_password = "mirror_rest_pass"
  }
}

machine_ids = []
endpoint_bindings = null
