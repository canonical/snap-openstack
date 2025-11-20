# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status

from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import (
    AddManifestStep,
    CharmManifest,
    SoftwareConfig,
)
from sunbeam.core.terraform import (
    TerraformInitStep,
)
from sunbeam.features.baremetal import commands, constants
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


class RunSetTempUrlSecretStep(BaseStep, JujuStepHelper):
    """Run the set-temp-url-secret action on the ironic-conductor apps."""

    def __init__(
        self,
        deployment: Deployment,
        feature: "BaremetalFeature",
        apps: None | list[str] = None,
    ):
        super().__init__(
            "Run the set-temp-url-secret action on ironic-conductor apps",
            "Running the set-temp-url-secret action on ironic-conductor apps",
        )
        self.deployment = deployment
        self.feature = feature
        self.apps = apps or [constants.IRONIC_CONDUCTOR_APP]

    def run(self, status: Status | None = None) -> Result:
        """Run the set-temp-url-secret action on ironic-conductor apps."""
        try:
            commands._run_set_temp_url_secret(
                self.feature,
                self.deployment,
                self.apps,
            )
        except Exception as e:
            LOG.error(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DeployNovaIronicShardsStep(BaseStep, JujuStepHelper):
    """Deploy nova-ironic shards using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: "BaremetalFeature",
        shards: list[str],
    ):
        super().__init__("Deploy nova-ironic shards", "Deploying nova-ironic shards")
        self.deployment = deployment
        self.feature = feature
        self.shards = shards

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        # item_name: charm_config
        items = {}
        for shard in self.shards:
            items[shard] = {"shard": shard}

        try:
            commands._baremetal_resource_add(
                self.feature,
                self.deployment,
                constants.NOVA_IRONIC_SHARDS_TFVAR,
                items,
                "nova-ironic",
                replace=True,
            )
        except Exception as ex:
            LOG.exception("Error deploying nova-ironic shards: %s", ex)
            return Result(ResultType.FAILED, str(ex))

        return Result(ResultType.COMPLETED)


class DeployIronicConductorGroupsStep(BaseStep, JujuStepHelper):
    """Deploy ironic-conductor groups using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        feature: "BaremetalFeature",
        conductor_groups: list[str],
    ):
        super().__init__(
            "Deploy ironic-conductor groups", "Deploying ironic-conductor groups"
        )
        self.deployment = deployment
        self.feature = feature
        self.conductor_groups = conductor_groups

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        # item_name: charm_config
        items = {}
        for conductor_group in self.conductor_groups:
            items[conductor_group] = {"conductor-group": conductor_group}

        try:
            commands._baremetal_resource_add(
                self.feature,
                self.deployment,
                constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
                items,
                "ironic-conductor",
                replace=True,
                apps_desired_status=["active", "blocked"],
            )
        except Exception as ex:
            LOG.exception("Error deploying ironic-conductor groups: %s", ex)
            return Result(ResultType.FAILED, str(ex))

        return Result(ResultType.COMPLETED)


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
                RunSetTempUrlSecretStep(
                    deployment,
                    self,
                ),
            ]
        )

        if config.shards:
            plan.append(
                DeployNovaIronicShardsStep(
                    deployment,
                    self,
                    config.shards,
                )
            )

        if config.conductor_groups:
            conductor_apps = [
                f"ironic-conductor-{name}" for name in config.conductor_groups
            ]
            plan.extend(
                [
                    DeployIronicConductorGroupsStep(
                        deployment,
                        self,
                        config.conductor_groups,
                    ),
                    RunSetTempUrlSecretStep(
                        deployment,
                        self,
                        conductor_apps,
                    ),
                ]
            )

        run_plan(plan, console, show_hints)

        click.echo("Baremetal enabled.")

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: BaremetalFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-ironic": True,
            "enable-ceph-rgw-ready": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-ironic": False,
            "enable-ceph-rgw-ready": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {},
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

    @click.group()
    def conductor_groups(self):
        """Manage baremetal ironic-conductor groups."""

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
                # Add the baremetal conductor-groups group:
                # sunbeam baremetal conductor-groups ...
                {"name": "conductor-groups", "command": self.conductor_groups},
            ],
            # Add the baremetal shard subcommands:
            "init.baremetal.shard": [
                # sunbeam baremetal shard action ...
                {"name": "add", "command": commands.compute_shard_add},
                {"name": "list", "command": commands.compute_shard_list},
                {"name": "delete", "command": commands.compute_shard_delete},
            ],
            # Add the baremetal conductor-groups subcommands:
            "init.baremetal.conductor-groups": [
                # sunbeam baremetal conductor-groups action ...
                {"name": "add", "command": commands.conductor_group_add},
                {"name": "list", "command": commands.conductor_group_list},
                {"name": "delete", "command": commands.conductor_group_delete},
            ],
        }
