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

# Create Juju secrets for Dell Storage Center backend credentials
# Main array credentials (required for all backends)
resource "juju_secret" "dellsc_san_credentials" {
  for_each = {
    for backend_name, backend_config in var.dellsc_backends :
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

# Secondary array credentials (optional, for dual DSM)
resource "juju_secret" "dellsc_secondary_san_credentials" {
  for_each = {
    for backend_name, backend_config in var.dellsc_backends :
    backend_name => backend_config
    if backend_config.secondary_san_username != "" && backend_config.secondary_san_password != ""
  }

  model = data.juju_model.machine_model.name
  name  = "${each.key}-secondary-san-credentials"
  value = {
    username = each.value.secondary_san_username
    password = each.value.secondary_san_password
  }
}

# Grant access to secrets for Dell Storage Center backend applications
resource "juju_access_secret" "dellsc_san_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.dellsc_backends :
    backend_name => backend_config
    if backend_config.san_username != "" && backend_config.san_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.dellsc_san_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.dellsc_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

resource "juju_access_secret" "dellsc_secondary_san_credentials_access" {
  for_each = {
    for backend_name, backend_config in var.dellsc_backends :
    backend_name => backend_config
    if backend_config.secondary_san_username != "" && backend_config.secondary_san_password != ""
  }

  model     = data.juju_model.machine_model.name
  secret_id = juju_secret.dellsc_secondary_san_credentials[each.key].secret_id
  applications = [each.key]
  
  # Ensure proper dependency ordering to avoid provider bugs
  depends_on = [juju_application.dellsc_backends]
  
  lifecycle {
    # Prevent destruction when applications list becomes empty
    prevent_destroy = false
    # Create before destroy to avoid empty applications list state
    create_before_destroy = true
  }
}

# Deploy Dell Storage Center backend charms
resource "juju_application" "dellsc_backends" {
  for_each = var.dellsc_backends

  name  = each.key
  model = data.juju_model.machine_model.name
  units = 1

  charm {
    name     = var.charm_dellsc_name
    channel  = var.charm_dellsc_channel
    revision = var.charm_dellsc_revision
    base     = var.charm_dellsc_base
  }

  config = merge({
    volume-backend-name = each.key
  }, each.value.charm_config, {
    # Main array credentials - always required
    san-credentials-secret = contains(keys(juju_secret.dellsc_san_credentials), each.key) ? juju_secret.dellsc_san_credentials[each.key].secret_id : ""
    
    # Secondary array credentials - only if dual DSM is configured
    secondary-san-credentials-secret = contains(keys(juju_secret.dellsc_secondary_san_credentials), each.key) ? juju_secret.dellsc_secondary_san_credentials[each.key].secret_id : ""
  })

  endpoint_bindings = var.endpoint_bindings
}

# Integrate Dell Storage Center backends with main cinder-volume
resource "juju_integration" "dellsc_to_cinder_volume" {
  for_each = var.dellsc_backends

  model = var.machine_model

  application {
    name     = juju_application.dellsc_backends[each.key].name
    endpoint = var.charm_dellsc_endpoint
  }

  application {
    name     = "cinder-volume"
    endpoint = var.charm_dellsc_endpoint
  }
}
