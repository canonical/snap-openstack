# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from pathlib import Path

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.core.common import (
    BaseStep,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
)
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.terraform import (
    TerraformInitStep,
)
from sunbeam.features.interface.v1.openstack import (
    DisableOpenStackApplicationStep,
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.features.shared_filesystem import manila_data
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

MANILA_DATA_DEPLOY_TIMEOUT = 600  # 10 minutes
MANILA_DATA_TFPLAN = "manila-data-plan"
MANILA_DATA_TFPLAN_DIR = "deploy-manila-data"
MANILA_DATA_CONFIG_KEY = "TerraformVarsFeatureManilaDataPlan"
MANILA_DATA_APP = "manila-data"

LOG = logging.getLogger(__name__)
console = Console()


class SharedFilesystemFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "shared-filesystem"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def __init__(self) -> None:
        super().__init__()
        self.tfplan_manila_data = MANILA_DATA_TFPLAN
        self.tfplan_manila_data_dir = MANILA_DATA_TFPLAN_DIR

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "manila-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "manila-cephfs-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "manila-data": CharmManifest(channel="2024.1/edge"),
            },
            terraform={
                self.tfplan_manila_data: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.tfplan_manila_data_dir
                ),
            },
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
                },
            },
            self.tfplan_manila_data: {
                "charms": {
                    "manila-data": {
                        "channel": "charm-manila-data-channel",
                        "revision": "charm-manila-data-revision",
                        "config": "charm-manila-data-config",
                    },
                },
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = [
            "manila",
            "manila-mysql-router",
            "manila-cephfs",
            "manila-cephfs-mysql-router",
            "manila-data-mysql-router",
        ]

        if self.get_database_topology(deployment) == "multi":
            apps.extend(["manila-mysql"])

        return apps

    def run_enable_plans(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ):
        """Run the enablement plans."""
        jhelper = JujuHelper(deployment.juju_controller)
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_manila_data = deployment.get_tfhelper(self.tfplan_manila_data)
        client = deployment.get_client()

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(AddManifestStep(deployment.get_client(), self.user_manifest))

        plan.extend(
            [
                TerraformInitStep(tfhelper),
                EnableOpenStackApplicationStep(
                    deployment,
                    config,
                    tfhelper,
                    jhelper,
                    self,
                ),
            ]
        )

        manila_data_plan = [
            TerraformInitStep(tfhelper_manila_data),
            manila_data.DeployManilaDataApplicationStep(
                deployment,
                client,
                tfhelper_manila_data,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console, show_hints)
        run_plan(manila_data_plan, console, show_hints)

        click.echo("Shared Filesystems enabled.")

    def run_disable_plans(self, deployment: Deployment, show_hints: bool):
        """Run the disablement plans."""
        jhelper = JujuHelper(deployment.juju_controller)
        tfhelper = deployment.get_tfhelper(self.tfplan)
        tfhelper_manila_data = deployment.get_tfhelper(self.tfplan_manila_data)

        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
        ]

        manila_data_plan = [
            TerraformInitStep(tfhelper_manila_data),
            manila_data.DestroyManilaDataApplicationStep(
                deployment.get_client(),
                tfhelper_manila_data,
                jhelper,
                self.manifest,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(manila_data_plan, console, show_hints)
        run_plan(plan, console, show_hints)

        click.echo("Shared Filesystems disabled.")

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
