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

import logging

import click
from packaging.version import Version

from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.manifest import CharmManifest, SoftwareConfig
from sunbeam.plugins.interface.v1.openstack import (
    OpenStackControlPlanePlugin,
    TerraformPlanLocation,
)
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)


class OrchestrationPlugin(OpenStackControlPlanePlugin):
    version = Version("0.0.1")

    def __init__(self, deployment: Deployment) -> None:
        super().__init__(
            "orchestration",
            deployment,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )

    def manifest_defaults(self) -> SoftwareConfig:
        """Plugin software configuration."""
        return SoftwareConfig(
            charms={"heat-k8s": CharmManifest(channel=OPENSTACK_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "heat-k8s": {
                        "channel": "heat-channel",
                        "revision": "heat-revision",
                        "config": "heat-config",
                    }
                }
            }
        }

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        apps = ["heat", "heat-mysql-router"]
        if self.get_database_topology() == "multi":
            apps.extend(["heat-mysql"])

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-heat": True,
            **self.add_horizon_plugin_to_tfvars("heat"),
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-heat": False,
            **self.remove_horizon_plugin_from_tfvars("heat"),
        }

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "heat": {"heat-k8s": 4},
        }

    @click.command()
    def enable_plugin(self) -> None:
        """Enable Orchestration service."""
        super().enable_plugin()

    @click.command()
    def disable_plugin(self) -> None:
        """Disable Orchestration service."""
        super().disable_plugin()
