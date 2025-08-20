# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import click
from packaging.version import Version

from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import CharmManifest, FeatureConfig, SoftwareConfig
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.v1.base import ConfigType, FeatureRequirement
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL


class SecretsFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.2")

    requires = {FeatureRequirement("vault>1")}
    name = "secrets"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={"barbican-k8s": CharmManifest(channel=OPENSTACK_CHANNEL)}
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "barbican-k8s": {
                        "channel": "barbican-channel",
                        "revision": "barbican-revision",
                        "config": "barbican-config",
                    }
                }
            }
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = ["barbican", "barbican-mysql-router"]
        if self.get_database_topology(deployment) == "multi":
            apps.append("barbican-mysql")

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-barbican": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {"enable-barbican": False}

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service."""
        return {
            "barbican": {"barbican-k8s": 9},
        }

    def is_vault_application_active(self, jhelper: JujuHelper) -> bool:
        """Check if Vault application is active."""
        application = jhelper.get_application("vault", OPENSTACK_MODEL)
        status = application.app_status.current
        if status == "active":
            return True
        return False

    def pre_enable(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Handler to perform tasks before enabling the feature."""
        super().pre_enable(deployment, config, show_hints)
        jhelper = JujuHelper(deployment.juju_controller)
        if not self.is_vault_application_active(jhelper):
            raise click.ClickException("Cannot enable secrets as Vault is not active.")

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable OpenStack Secrets service."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable OpenStack Secrets service."""
        self.disable_feature(deployment, show_hints)
