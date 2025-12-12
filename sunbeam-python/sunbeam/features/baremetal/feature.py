# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

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
    SoftwareConfig,
)
from sunbeam.core.terraform import (
    TerraformInitStep,
)
from sunbeam.features.baremetal import constants, steps
from sunbeam.features.baremetal.feature_config import (
    BaremetalFeatureConfig,
)
from sunbeam.features.interface.v1.openstack import (
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import OPENSTACK_CHANNEL

LOG = logging.getLogger(__name__)
console = Console()


class BaremetalFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "baremetal"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def config_type(self) -> type | None:
        """Return the config type for the feature."""
        return BaremetalFeatureConfig

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                "ironic-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "nova-ironic-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
                "ironic-conductor-k8s": CharmManifest(channel=OPENSTACK_CHANNEL),
            },
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    "ironic-k8s": {
                        "channel": "ironic-channel",
                        "revision": "ironic-revision",
                        "config": "ironic-config",
                    },
                    "nova-ironic-k8s": {
                        "channel": "nova-ironic-channel",
                        "revision": "nova-ironic-revision",
                        "config": "nova-ironic-config",
                    },
                    "ironic-conductor-k8s": {
                        "channel": "ironic-conductor-channel",
                        "revision": "ironic-conductor-revision",
                        "config": "ironic-conductor-config",
                    },
                },
            },
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        apps = [
            "ironic",
            "ironic-mysql-router",
            "nova-ironic",
            "nova-ironic-mysql-router",
            "ironic-conductor",
            "ironic-conductor-mysql-router",
        ]

        if self.get_database_topology(deployment) == "multi":
            apps.extend(["ironic-mysql"])

        return apps

    def run_enable_plans(
        self, deployment: Deployment, config: BaremetalFeatureConfig, show_hints: bool
    ):
        """Run the enablement plans."""
        jhelper = JujuHelper(deployment.juju_controller)
        tfhelper = deployment.get_tfhelper(self.tfplan)

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
                    # ironic-conductor-k8s charm will be in the blocked state
                    # until we run the set-temp-url-secret action.
                    app_desired_status=["active", "blocked"],
                ),
                steps.RunSetTempUrlSecretStep(
                    deployment,
                    jhelper,
                ),
            ]
        )

        if config.shards:
            plan.append(
                steps.DeployNovaIronicShardsStep(
                    deployment,
                    self,
                    config.shards,
                    replace=True,
                )
            )

        run_plan(plan, console, show_hints)

        click.echo("Baremetal enabled.")

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: BaremetalFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-ironic": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-ironic": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: BaremetalFeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable Baremetal service."""
        ctx = click.get_current_context()

        # The parent context (enable context) has the --manifest parameter.
        manifest_path = None
        if ctx.parent:
            manifest_path = ctx.parent.params["manifest"]

        manifest = deployment.get_manifest(manifest_path)
        feature = manifest.get_feature(self.name)
        if feature:
            config = feature.config
        else:
            config = BaremetalFeatureConfig()

        self.enable_feature(deployment, config, show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable Baremetal service."""
        self.disable_feature(deployment, show_hints)

    @click.group()
    def baremetal_group(self):
        """Manage baremetal feature."""

    @click.group()
    def shard_group(self):
        """Manage baremetal nova-compute shards."""

    @click.command()
    @click.argument("shard")
    @click_option_show_hints
    @pass_method_obj
    def compute_shard_add(self, deployment: Deployment, shard: str, show_hints: bool):
        """Add Ironic nova-compute shard."""
        step = steps.DeployNovaIronicShardsStep(deployment, self, [shard])
        run_plan([step], console, show_hints)

    @click.command()
    @pass_method_obj
    def compute_shard_list(self, deployment: Deployment):
        """List Ironic nova-compute shards."""
        step = steps.ListNovaIronicShardsStep(deployment, self)
        run_plan([step], console)

    @click.command()
    @click.argument("shard")
    @click_option_show_hints
    @pass_method_obj
    def compute_shard_delete(
        self, deployment: Deployment, shard: str, show_hints: bool
    ):
        """Delete Ironic nova-compute shard."""
        step = steps.DeleteNovaIronicShardStep(deployment, self, shard)
        run_plan([step], console, show_hints)

    def enabled_commands(self) -> dict[str, list[dict]]:
        """Dict of clickgroup along with commands.

        Return the commands available once the feature is enabled.
        """
        return {
            # Add the baremetal subcommand group to the root group:
            "init": [{"name": "baremetal", "command": self.baremetal_group}],
            # Add the baremetal subcommands:
            "init.baremetal": [
                # Add the baremetal shard group:
                # sunbeam baremetal shard ...
                {"name": "shard", "command": self.shard_group},
            ],
            # Add the baremetal shard subcommands:
            "init.baremetal.shard": [
                # sunbeam baremetal shard action ...
                {"name": "add", "command": self.compute_shard_add},
                {"name": "list", "command": self.compute_shard_list},
                {"name": "delete", "command": self.compute_shard_delete},
            ],
        }
