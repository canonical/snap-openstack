# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from packaging.version import Version

from sunbeam.core.common import RiskLevel
from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import CharmManifest, FeatureConfig, SoftwareConfig
from sunbeam.features.interface.v1.base import FeatureRequirement
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)


class RatingFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")
    requires = {FeatureRequirement("telemetry")}
    risk_availability = RiskLevel.BETA

    name = "rating"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={"cloudkitty-k8s": CharmManifest(channel=OPENSTACK_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "cloudkitty-k8s": {
                        "channel": "cloudkitty-channel",
                        "revision": "cloudkitty-revision",
                        "config": "cloudkitty-config",
                    }
                }
            }
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = ["cloudkitty", "cloudkitty-mysql-router"]
        if self.get_database_topology(deployment) == "multi":
            apps.extend(["cloudkitty-mysql"])

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-cloudkitty": True,
            **self.add_horizon_plugin_to_tfvars(deployment, "cloudkitty"),
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-cloudkitty": False,
            **self.remove_horizon_plugin_from_tfvars(deployment, "cloudkitty"),
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "cloudkitty": {"cloudkitty-k8s": 2},
        }

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable Rating service."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Rating service."""
        self.disable_feature(deployment, show_hints)
