# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from packaging.version import Version

from sunbeam.core.deployment import Deployment
from sunbeam.core.manifest import (
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
)
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)


class SharedFilesystemFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "shared-filesystem"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "manila-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "manila-cephfs-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
            }
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "manila-k8s": {
                        "channel": "manila-channel",
                        "revision": "manila-revision",
                        "config": "manila-config",
                    },
                    "manila-cephfs-k8s": {
                        "channel": "manila-cephfs-channel",
                        "revision": "manila-cephfs-revision",
                        "config": "manila-cephfs-config",
                    },
                }
            }
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = [
            "manila",
            "manila-mysql-router",
            "manila-cephfs",
            "manila-cephfs-mysql-router",
        ]

        if self.get_database_topology(deployment) == "multi":
            apps.extend(["manila-mysql"])

        return apps

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-manila": True,
            "enable-manila-cephfs": True,
            "enable-ceph-nfs": True,
            **self.add_horizon_plugin_to_tfvars(deployment, "manila"),
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-manila": False,
            "enable-manila-cephfs": False,
            "enable-ceph-nfs": False,
            **self.remove_horizon_plugin_from_tfvars(deployment, "manila"),
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable Shared Filesystems service."""
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Shared Filesystems service."""
        self.disable_feature(deployment, show_hints)
