# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

output "dellsc_backend_applications" {
  description = "Map of deployed Dell Storage Center backend applications"
  value = {
    for name, app in juju_application.dellsc_backends : name => {
      name  = app.name
      model = app.model
      units = app.units
    }
  }
}
