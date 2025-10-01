# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

output "hitachi_backend_applications" {
  description = "Map of deployed Hitachi backend applications"
  value = {
    for name, app in juju_application.hitachi_backends : name => {
      name  = app.name
      model = app.model
      units = app.units
    }
  }
}
