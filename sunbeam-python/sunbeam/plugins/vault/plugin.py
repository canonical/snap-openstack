# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Vault plugin.

Vault secure, store and tightly control access to tokens, passwords, certificates,
encryption keys for protecting secrets and other sensitive data.
"""

import logging

import click
from packaging.version import Version

from sunbeam.clusterd.client import Client
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    TerraformPlanLocation,
)

LOG = logging.getLogger(__name__)


class VaultPlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")

    def __init__(self, client: Client) -> None:
        super().__init__(
            "vault",
            client,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        return ["vault"]

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-vault": True,
            "vault-channel": "latest/edge",
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-vault": False}

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Vault.

        Vault secure, store and tightly control access to tokens, passwords,
        certificates, encryption keys for protecting secrets and other sensitive data.
        """
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable Vault."""
        super().disable_plugin()
