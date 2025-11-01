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
    update_config,
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
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.features.baremetal import commands, constants, feature_config
from sunbeam.features.interface.v1.openstack import (
    EnableOpenStackApplicationStep,
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

# TODO: Remove this when the charms are on the 2024.1 channel.
OPENSTACK_CHANNEL = "2025.1/edge"

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
            LOG.error(str(ex))
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


class UpdateSwitchConfigSecretsStep(BaseStep, JujuStepHelper):
    """Update Neutron baremetal / generic switch config secrets."""

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        feature: "BaremetalFeature",
        switch_configs: feature_config._SwitchConfigs,
    ):
        super().__init__(
            "Update neutron baremetal / generic switch configs",
            "Updating neutron baremetal / generic switch configs",
        )
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.switch_configs = switch_configs

    def run(self, status: Status | None = None) -> Result:
        """Update juju secrets containing switch configs."""
        try:
            tfvars = commands._get_tfvars(self.feature, self.deployment)
            tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
            conf_secrets = tfvars.get(tfvars_key, {})
            tfvars[tfvars_key] = conf_secrets

            # Remove existing secrets and add new ones.
            secrets = conf_secrets.get("netconf", [])
            self._remove_secrets(secrets)
            secret_ids = self._add_secrets("netconf", self.switch_configs.netconf)
            opt = ",".join(secret_ids)
            tfvars[constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR] = opt
            new_names = self.switch_configs.netconf.keys()
            conf_secrets["netconf"] = [f"switch-config-{name}" for name in new_names]

            secrets = conf_secrets.get("generic", [])
            self._remove_secrets(secrets)
            secret_ids = self._add_secrets("generic", self.switch_configs.generic)
            opt = ",".join(secret_ids)
            tfvars[constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR] = opt
            new_names = self.switch_configs.generic.keys()
            conf_secrets["generic"] = [f"switch-config-{name}" for name in new_names]

            # write terraform changes.
            client = self.deployment.get_client()
            config_key = self.feature.get_tfvar_config_key()
            self.tfhelper.write_tfvars(tfvars)
            update_config(client, config_key, tfvars)
        except Exception as ex:
            LOG.error("Encountered error: %s", ex)
            return Result(ResultType.FAILED, str(ex))

        return Result(ResultType.COMPLETED)

    def _remove_secrets(self, secrets_list: list[str]):
        for secret in secrets_list:
            self.jhelper.remove_secret(OPENSTACK_MODEL, secret)
            secrets_list.remove(secret)

    def _add_secrets(self, protocol: str, configs: dict[str, feature_config._Config]):
        config_charm = "neutron-baremetal-switch-config"
        if protocol == "generic":
            config_charm = "neutron-generic-switch-config"

        secret_ids = []
        for config_name, config in configs.items():
            secret_name = f"switch-config-{config_name}"
            secret_data = {
                "conf": config.configfile,
                **config.additional_files,
            }

            secret_id = self.jhelper.add_secret(
                OPENSTACK_MODEL,
                secret_name,
                secret_data,
                "Neutron switch config",
            )

            for app in ["neutron", config_charm]:
                self.jhelper.grant_secret(OPENSTACK_MODEL, secret_name, app)

            secret_ids.append(secret_id)

        return secret_ids


class BaremetalFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "baremetal"
    tf_plan_location = TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO

    def config_type(self) -> type | None:
        """Return the config type for the feature."""
        return feature_config.BaremetalFeatureConfig

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
        config: feature_config.BaremetalFeatureConfig,
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
                UpdateSwitchConfigSecretsStep(
                    deployment,
                    tfhelper,
                    jhelper,
                    self,
                    config.switchconfigs or feature_config._SwitchConfigs(),
                ),
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
        self, deployment: Deployment, config: feature_config.BaremetalFeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        return {
            "enable-ironic": True,
            "enable-ceph-rgw": True,
        }

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        return {
            "enable-ironic": False,
            "enable-ceph-rgw": False,
            constants.NOVA_IRONIC_SHARDS_TFVAR: {},
            constants.IRONIC_CONDUCTOR_GROUPS_TFVAR: {},
            constants.NEUTRON_BAREMETAL_SWITCH_CONF_SECRETS_TFVAR: "",
            constants.NEUTRON_GENERIC_SWITCH_CONF_SECRETS_TFVAR: "",
        }

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: feature_config.BaremetalFeatureConfig
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
            config = feature_config.BaremetalFeatureConfig()

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

    @click.group()
    def switch_config_group(self):
        """Manage baremetal switch configurations."""

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
            # Add the baremetal switch-config subcommands:
            "init.baremetal.switch-config": [
                # sunbeam baremetal switch-config action ...
                {"name": "add", "command": commands.switch_config_add},
                {"name": "list", "command": commands.switch_config_list},
                {"name": "update", "command": commands.switch_config_update},
                {"name": "delete", "command": commands.switch_config_delete},
            ],
        }
