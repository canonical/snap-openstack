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

from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.manifest import CharmManifest, SoftwareConfig
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)


class LoadbalancerFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    def __init__(self, deployment: Deployment) -> None:
        super().__init__(
            "loadbalancer",
            deployment,
            tf_plan_location=TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO,
        )

    def manifest_defaults(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={"octavia-k8s": CharmManifest(channel=OPENSTACK_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "octavia-k8s": {
                        "channel": "octavia-channel",
                        "revision": "octavia-revision",
                        "config": "octavia-config",
                    }
                }
            }
        }

    def set_application_names(self) -> list:
        """Application names handled by the terraform plan."""
        apps = ["octavia", "octavia-mysql-router"]
        if self.get_database_topology() == "multi":
            apps.extend(["octavia-mysql"])

        return apps

    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-octavia": True,
            **self.add_horizon_plugin_to_tfvars("octavia"),
        }

    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-octavia": False,
            **self.remove_horizon_plugin_from_tfvars("octavia"),
        }

    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "octavia": {"octavia-k8s": 6},
        }

    @click.command()
    def enable_feature(self) -> None:
        """Enable Loadbalancer service."""
        super().enable_feature()

    @click.command()
    def disable_feature(self) -> None:
        """Disable Loadbalancer service."""
        super().disable_feature()
