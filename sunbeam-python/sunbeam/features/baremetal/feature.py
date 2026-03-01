# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing

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
from sunbeam.features.baremetal import constants, feature_config, steps
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
    generally_available = False
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
                "neutron-baremetal-switch-config-k8s": CharmManifest(
                    channel=OPENSTACK_CHANNEL
                ),
                "neutron-generic-switch-config-k8s": CharmManifest(
                    channel=OPENSTACK_CHANNEL
                ),
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
                    "neutron-baremetal-switch-config-k8s": {
                        "channel": "neutron-baremetal-switch-config-channel",
                        "revision": "neutron-baremetal-switch-config-revision",
                    },
                    "neutron-generic-switch-config-k8s": {
                        "channel": "neutron-generic-switch-config-channel",
                        "revision": "neutron-generic-switch-config-revision",
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
            "neutron-baremetal-switch-config",
            "neutron-generic-switch-config",
        ]

        if self.get_database_topology(deployment) == "multi":
            apps.extend(["ironic-mysql"])

        return apps

    def run_enable_plans(
        self,
        deployment: Deployment,
        config: BaremetalFeatureConfig,
        show_hints: bool,
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
                steps.UpdateSwitchConfigSecretsStep(
                    deployment,
                    self,
                    config.switchconfigs or feature_config._SwitchConfigs(),
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

        if config.conductor_groups:
            conductor_apps = [
                f"ironic-conductor-{name}" for name in config.conductor_groups
            ]
            plan.extend(
                [
                    steps.DeployIronicConductorGroupsStep(
                        deployment,
                        self,
                        config.conductor_groups,
                    ),
                    steps.RunSetTempUrlSecretStep(
                        deployment,
                        jhelper,
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
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-ironic": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {},
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "",
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "",
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

    @click.group()
    def conductor_groups(self):
        """Manage baremetal ironic-conductor groups."""

    @click.command()
    @click.argument("group_name")
    @click_option_show_hints
    @pass_method_obj
    def conductor_group_add(
        self, deployment: Deployment, group_name: str, show_hints: bool
    ):
        """Add ironic-conductor group."""
        step = steps.DeployIronicConductorGroupsStep(deployment, self, [group_name])
        jhelper = JujuHelper(deployment.juju_controller)
        temp_url_secret_step = steps.RunSetTempUrlSecretStep(
            deployment,
            jhelper,
            [f"ironic-conductor-{group_name}"],
        )
        run_plan([step, temp_url_secret_step], console, show_hints)

    @click.command()
    @pass_method_obj
    def conductor_group_list(self, deployment: Deployment):
        """List ironic-conductor groups."""
        step = steps.ListIronicConductorGroupsStep(deployment, self)
        run_plan([step], console)

    @click.command()
    @click.argument("group_name")
    @click_option_show_hints
    @pass_method_obj
    def conductor_group_delete(
        self, deployment: Deployment, group_name: str, show_hints: bool
    ):
        """Delete ironic-conductor group."""
        step = steps.DeleteIronicConductorGroupStep(deployment, self, group_name)
        run_plan([step], console, show_hints)

    @click.group()
    def switch_config_group(self):
        """Manage baremetal switch configurations."""

    @click.command()
    @click.argument("protocol", type=click.Choice(["netconf", "generic"]))
    @click.argument("name")
    @click.option(
        "--config",
        required=True,
        type=click.File("r"),
        metavar="FILEPATH",
        help="The path to a baremetal / generic switch config file.",
    )
    @click.option(
        "--additional-file",
        "additional_files",
        multiple=True,
        type=(str, click.File("r")),
        metavar="<NAME FILEPATH>",
        help=(
            "The name and path pair to an additional file. "
            "Can be repeated for multiple files"
        ),
    )
    @click_option_show_hints
    @pass_method_obj
    def switch_config_add(
        self,
        deployment: Deployment,
        protocol: str,
        name: str,
        config: typing.TextIO,
        additional_files: list[tuple[str, typing.TextIO]],
        show_hints: bool,
    ):
        """Add Neutron baremetal / generic switch configuration."""
        switch_configs = feature_config._SwitchConfigs.read_switch_config(
            name,
            protocol,
            config,
            additional_files,
        )
        config_obj = getattr(switch_configs, protocol)[name]

        step = steps.AddSwitchConfigStep(deployment, self, protocol, name, config_obj)
        run_plan([step], console, show_hints)

    @click.command()
    @pass_method_obj
    def switch_config_list(*args, **kwargs):
        """List Neutron baremetal / generic switch configurations."""
        step = steps.ListSwitchConfigsStep(deployment, self)
        run_plan([step], console)

    @click.command()
    @click.argument("protocol", type=click.Choice(["netconf", "generic"]))
    @click.argument("name")
    @click.option(
        "--config",
        required=True,
        type=click.File("r"),
        metavar="FILEPATH",
        help="The path to a baremetal / generic switch config file.",
    )
    @click.option(
        "--additional-file",
        "additional_files",
        multiple=True,
        type=(str, click.File("r")),
        metavar="<NAME FILEPATH>",
        help=(
            "The name and path pair to an additional file. "
            "Can be repeated for multiple files"
        ),
    )
    @click_option_show_hints
    @pass_method_obj
    def switch_config_update(
        self,
        deployment: Deployment,
        protocol: str,
        name: str,
        config: typing.TextIO,
        additional_files: list[tuple[str, typing.TextIO]],
        show_hints: bool,
    ):
        """Update Neutron baremetal / generic switch configuration."""
        switch_configs = feature_config._SwitchConfigs.read_switch_config(
            name,
            protocol,
            config,
            additional_files,
        )
        config_obj = getattr(switch_configs, protocol)[name]

        step = steps.UpdateSwitchConfigStep(
            deployment, self, protocol, name, config_obj
        )
        run_plan([step], console, show_hints)

    @click.command()
    @click.argument("name")
    @click_option_show_hints
    @pass_method_obj
    def switch_config_delete(*args, **kwargs):
        """Delete Neutron baremetal / generic switch configuration."""
        step = steps.DeleteSwitchConfigStep(deployment, self, name)
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
                # Add the baremetal conductor-groups group:
                # sunbeam baremetal conductor-groups ...
                {"name": "conductor-groups", "command": self.conductor_groups},
                # Add the baremetal switch-config group:
                # sunbeam baremetal switch-config ...
                {"name": "switch-config", "command": self.switch_config_group},
            ],
            # Add the baremetal shard subcommands:
            "init.baremetal.shard": [
                # sunbeam baremetal shard action ...
                {"name": "add", "command": self.compute_shard_add},
                {"name": "list", "command": self.compute_shard_list},
                {"name": "delete", "command": self.compute_shard_delete},
            ],
            # Add the baremetal conductor-groups subcommands:
            "init.baremetal.conductor-groups": [
                # sunbeam baremetal conductor-groups action ...
                {"name": "add", "command": self.conductor_group_add},
                {"name": "list", "command": self.conductor_group_list},
                {"name": "delete", "command": self.conductor_group_delete},
            ],
            # Add the baremetal switch-config subcommands:
            "init.baremetal.switch-config": [
                # sunbeam baremetal switch-config action ...
                {"name": "add", "command": self.switch_config_add},
                {"name": "list", "command": self.switch_config_list},
                {"name": "update", "command": self.switch_config_update},
                {"name": "delete", "command": self.switch_config_delete},
            ],
        }
