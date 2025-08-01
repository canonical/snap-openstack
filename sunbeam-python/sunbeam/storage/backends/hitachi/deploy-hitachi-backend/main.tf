# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "= 0.20.0"
    }
  }
}

provider "juju" {}

data "juju_model" "machine_model" {
  name = var.machine_model
}

# Create Juju secrets for Hitachi backend credentials
# Main array credentials (required for all backends)
resource "juju_secret" "hitachi_san_credentials" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.san_username != "" && backend_config.san_password != ""
  }

  model = data.juju_model.machine_model.name
  name  = "${each.key}-san-credentials"
  value = {
    username = each.value.san_username
    password = each.value.san_password
  }
}

# CHAP credentials (optional, only for iSCSI with CHAP auth)
resource "juju_secret" "hitachi_chap_credentials" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.use_chap_auth == true && backend_config.chap_username != "" && backend_config.chap_password != ""
  }

  model = data.juju_model.machine_model.name
  name  = "${each.key}-chap-credentials"
  value = {
    username = each.value.chap_username
    password = each.value.chap_password
  }
}

# Mirror CHAP credentials (optional, for GAD replication)
resource "juju_secret" "hitachi_mirror_chap_credentials" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.hitachi_mirror_chap_username != "" && backend_config.hitachi_mirror_chap_password != ""
  }

  model = data.juju_model.machine_model.name
  name  = "${each.key}-mirror-chap-credentials"
  value = {
    username = each.value.hitachi_mirror_chap_username
    password = each.value.hitachi_mirror_chap_password
  }
}

# Mirror REST API credentials (optional, for GAD replication)
resource "juju_secret" "hitachi_mirror_rest_credentials" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.hitachi_mirror_rest_username != "" && backend_config.hitachi_mirror_rest_password != ""
  }

  model = data.juju_model.machine_model.name
  name  = "${each.key}-mirror-rest-credentials"
  value = {
    username = each.value.hitachi_mirror_rest_username
    password = each.value.hitachi_mirror_rest_password
  }
}

# Grant access to secrets for Hitachi backend applications
resource "juju_access_secret" "hitachi_san_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.san_username != "" && backend_config.san_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.hitachi_san_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.hitachi_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

resource "juju_access_secret" "hitachi_chap_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.use_chap_auth == true && backend_config.chap_username != "" && backend_config.chap_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.hitachi_chap_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.hitachi_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

resource "juju_access_secret" "hitachi_mirror_chap_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.hitachi_mirror_chap_username != "" && backend_config.hitachi_mirror_chap_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.hitachi_mirror_chap_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.hitachi_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

resource "juju_access_secret" "hitachi_mirror_rest_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.hitachi_backends :
    backend_name => backend_config
    if backend_config.hitachi_mirror_rest_username != "" && backend_config.hitachi_mirror_rest_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.hitachi_mirror_rest_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.hitachi_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

# Deploy Hitachi storage backend charms
resource "juju_application" "hitachi_backends" {
  for_each = var.hitachi_backends

  name  = each.key
  model = data.juju_model.machine_model.name
  units = 1

  charm {
    name     = "cinder-volume-hitachi"
    channel  = var.charm_hitachi_channel
    revision = var.charm_hitachi_revision
    base     = "ubuntu@24.04"
  }

  config = merge({
    volume-backend-name = each.key
  }, each.value.charm_config, {
    # Main array credentials - always required
    san-credentials-secret = contains(keys(juju_secret.hitachi_san_credentials), each.key) ? juju_secret.hitachi_san_credentials[each.key].secret_id : ""
    
    # CHAP credentials - only if CHAP auth is enabled and credentials provided
    chap-credentials-secret = contains(keys(juju_secret.hitachi_chap_credentials), each.key) ? juju_secret.hitachi_chap_credentials[each.key].secret_id : ""
    
    # Mirror CHAP credentials - only if mirror CHAP credentials provided
    hitachi-mirror-chap-credentials-secret = contains(keys(juju_secret.hitachi_mirror_chap_credentials), each.key) ? juju_secret.hitachi_mirror_chap_credentials[each.key].secret_id : ""
    
    # Mirror REST API credentials - only if mirror REST credentials provided
    hitachi-mirror-rest-credentials-secret = contains(keys(juju_secret.hitachi_mirror_rest_credentials), each.key) ? juju_secret.hitachi_mirror_rest_credentials[each.key].secret_id : ""
  })

  endpoint_bindings = var.endpoint_bindings
}

# Integrate Hitachi backends with main cinder-volume
resource "juju_integration" "hitachi_to_cinder_volume" {
  for_each = var.hitachi_backends

  model = var.machine_model

  application {
    name     = juju_application.hitachi_backends[each.key].name
    endpoint = "cinder-volume"
  }

  application {
    name     = "cinder-volume"
    endpoint = "cinder-volume"
  }
}
