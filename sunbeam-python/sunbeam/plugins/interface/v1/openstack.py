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


import inspect
import logging
import shutil
from abc import abstractmethod
from enum import Enum
from pathlib import Path
from typing import Optional, TypedDict

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.openstack import (
    OPENSTACK_MODEL,
    determine_target_topology_at_bootstrap,
)
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.checks import VerifyBootstrappedCheck
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    delete_config,
    read_config,
    run_plan,
    run_preflight_checks,
    update_config,
)
from sunbeam.jobs.juju import (
    ChannelUpdate,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)
from sunbeam.plugins.interface.v1.base import EnableDisablePlugin

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
OPENSTACK_TERRAFORM_VARS = "TerraformVarsOpenstack"
OPENSTACK_TERRAFORM_PLAN = "openstack"


class ApplicationChannelData(TypedDict):
    """Application channel data.

    channel is the charm channel that is inline with this snap
    tfvars_channel_var is the terraform variable used to store the channel.
    """

    channel: str
    tfvars_channel_var: str | None


class TerraformPlanLocation(Enum):
    """Enum to define Terraform plan location

    There are 2 choices - either in sunbeam-terraform repo or
    part of plugin in etc/deploy-<plugin name> directory.
    """

    SUNBEAM_TERRAFORM_REPO = 1
    PLUGIN_REPO = 2


class OpenStackControlPlanePlugin(EnableDisablePlugin):
    """Interface for plugins to manage OpenStack Control plane components.

    Plugins that manages OpenStack control plane components using terraform
    plans can use this interface.

    The terraform plans can be defined either in sunbeam-terraform repo or as part
    of the plugin in specific directory etc/deploy-<plugin name>.
    """

    interface_version = Version("0.0.1")

    def __init__(
        self, name: str, client: Client, tf_plan_location: TerraformPlanLocation
    ) -> None:
        """Constructor for plugin interface.

        :param name: Name of the plugin
        :param tf_plan_location: Location where terraform plans are placed
        """
        super().__init__(name, client)
        self.app_name = self.name.capitalize()
        self.tf_plan_location = tf_plan_location

        # Based on terraform plan location, tfplan will be either
        # openstack or plugin name
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            self.tfplan = OPENSTACK_TERRAFORM_PLAN
        else:
            self.tfplan = self.name

        self.snap = Snap()

    def _get_tf_plan_full_path(self) -> Path:
        """Returns terraform plan absolute path."""
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            return self.snap.paths.snap / "etc" / f"deploy-{self.tfplan}"
        else:
            plugin_class_dir = Path(inspect.getfile(self.__class__)).parent
            return plugin_class_dir / "etc" / f"deploy-{self.tfplan}"

    def _get_plan_name(self) -> str:
        """Returns plan name in format defined in cluster db."""
        return f"{self.tfplan}-plan"

    def is_openstack_control_plane(self) -> bool:
        """Is plugin deploys openstack control plane.

        :returns: True if plugin deploys openstack control plane, else False.
        """
        return True

    def get_terraform_openstack_plan_path(self) -> Path:
        """Return Terraform OpenStack plan location."""
        return self.get_terraform_plans_base_path() / "etc" / "deploy-openstack"

    def pre_checks(self) -> None:
        """Perform preflight checks before enabling the plugin.

        Also copies terraform plans to required locations.
        """
        preflight_checks = []
        preflight_checks.append(VerifyBootstrappedCheck(self.client))
        run_preflight_checks(preflight_checks, console)
        src = self._get_tf_plan_full_path()
        dst = self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}"
        LOG.debug(f"Updating {dst} from {src}...")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def pre_enable(self) -> None:
        """Handler to perform tasks before enabling the plugin."""
        self.pre_checks()
        super().pre_enable()

    def get_tfhelper(self):
        data_location = self.snap.paths.user_data
        tfhelper = TerraformHelper(
            path=self.snap.paths.user_common / "etc" / f"deploy-{self.tfplan}",
            plan=self._get_plan_name(),
            backend="http",
            data_location=data_location,
        )
        return tfhelper

    def run_enable_plans(self) -> None:
        """Run plans to enable plugin."""
        data_location = self.snap.paths.user_data
        tfhelper = self.get_tfhelper()
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            TerraformInitStep(tfhelper),
            EnableOpenStackApplicationStep(self.client, tfhelper, jhelper, self),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application enabled.")

    def pre_disable(self) -> None:
        """Handler to perform tasks before disabling the plugin."""
        self.pre_checks()
        super().pre_disable()

    def run_disable_plans(self) -> None:
        """Run plans to disable the plugin."""
        data_location = self.snap.paths.user_data
        tfhelper = self.get_tfhelper()
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(self.client, tfhelper, jhelper, self),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application disabled.")

    def get_tfvar_config_key(self) -> str:
        """Returns Config key to save terraform vars.

        If the terraform plans are in sunbeam-terraform repo, use the config
        key defined by the plan DeployOpenStackControlPlane i.e.,
        TerraformVarsOpenstack.
        If the terraform plans are part of plugin directory, use config key
        TerraformVars-<plugin name>.
        """
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            return OPENSTACK_TERRAFORM_VARS
        else:
            return f"TerraformVars{self.app_name}"

    def get_database_topology(self) -> str:
        """Returns the database topology of the cluster."""
        # Database topology can be set only during bootstrap and cannot be changed.
        return determine_target_topology_at_bootstrap()

    def set_application_timeout_on_enable(self) -> int:
        """Set Application Timeout on enabling the plugin.

        The plugin plan will timeout if the applications
        are not in active status within in this time.
        """
        return APPLICATION_DEPLOY_TIMEOUT

    def set_application_timeout_on_disable(self) -> int:
        """Set Application Timeout on disabling the plugin.

        The plugin plan will timeout if the applications
        are not removed within this time.
        """
        return APPLICATION_DEPLOY_TIMEOUT

    @abstractmethod
    def set_application_names(self) -> list:
        """Application names handled by the terraform plan.

        Returns list of applications that are deployed by the
        terraform plan during enable. During disable, these
        applications should get destroyed.
        """

    @abstractmethod
    def set_tfvars_on_enable(self) -> dict:
        """Set terraform variables to enable the application."""

    @abstractmethod
    def set_tfvars_on_disable(self) -> dict:
        """Set terraform variables to disable the application."""

    @abstractmethod
    def set_tfvars_on_resize(self) -> dict:
        """Set terraform variables to resize the application."""

    @abstractmethod
    def enable_plugin(self) -> None:
        """Enable plugin command."""
        super().enable_plugin()

    @abstractmethod
    def disable_plugin(self) -> None:
        """Disable plugin command."""
        super().disable_plugin()

    def add_horizon_plugin_to_tfvars(self, plugin: str) -> dict[str, list[str]]:
        """Tf vars to have the given plugin enabled.

        Return of the function is expected to be passed to set_tfvars_on_enable.
        """
        try:
            tfvars = read_config(self.client, self.get_tfvar_config_key())
        except ConfigItemNotFoundException:
            tfvars = {}

        horizon_plugins = tfvars.get("horizon-plugins", [])
        if plugin not in horizon_plugins:
            horizon_plugins.append(plugin)

        return {"horizon-plugins": sorted(horizon_plugins)}

    def remove_horizon_plugin_from_tfvars(self, plugin: str) -> dict[str, list[str]]:
        """TF vars to have the given plugin disabled.

        Return of the function is expected to be passed to set_tfvars_on_disable.
        """
        try:
            tfvars = read_config(self.client, self.get_tfvar_config_key())
        except ConfigItemNotFoundException:
            tfvars = {}

        horizon_plugins = tfvars.get("horizon-plugins", [])
        if plugin in horizon_plugins:
            horizon_plugins.remove(plugin)

        return {"horizon-plugins": sorted(horizon_plugins)}

    @property
    def k8s_application_data(self) -> dict[str, ApplicationChannelData]:
        """Mapping of k8s applications to their required channels."""
        return {}

    @property
    def machine_application_data(self) -> dict[str, ApplicationChannelData]:
        """Mapping of machine applications to their required channels."""
        return {}

    def upgrade_hook(self, upgrade_release: bool = False):
        """Run upgrade.

        :param upgrade_release: Whether to upgrade release
        """
        data_location = self.snap.paths.user_data
        tfhelper = self.get_tfhelper()
        jhelper = JujuHelper(self.client, data_location)
        plan = [
            UpgradeApplicationStep(
                self.client, tfhelper, jhelper, self, upgrade_release
            ),
        ]

        run_plan(plan, console)


class UpgradeApplicationStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
        upgrade_release: bool = False,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Refresh OpenStack {plugin.name}",
            f"Refresh OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL
        self.upgrade_release = upgrade_release

    def terraform_sync(self, config_key: str, tfvars_delta: dict):
        """Sync the running state back to the Terraform state file.

        :param config_key: The config key used to access the data in
                           microcluster
        :param tfvars_delta: The delta of changes to be applied to the
                             terraform vars stored in microcluster.
        """
        tfvars = read_config(self.client, config_key)
        tfvars.update(tfvars_delta)
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)
        self.tfhelper.sync()

    def upgrade_charms(
        self,
        application_data: dict[str, dict[str, str]],
        model: str,
    ):
        """Upgrade applications.

        :param application_data: Mapping of applications to their required channels
        :param model: Name of model applications are in
        """
        for application_name, config in application_data.items():
            if self.revision_update_needed(application_name, model):
                run_sync(self.jhelper.charm_refresh(application_name, model))
        if self.upgrade_release:
            batch = {}
            expect_wls = {"workload": ["blocked", "active"]}
            for application_name, config in application_data.items():
                current_channel = run_sync(
                    self.jhelper.get_charm_channel(application_name, model)
                )
                new_channel = config["channel"]
                LOG.debug(
                    f"new_channel: {new_channel} current_channel: {current_channel}"
                )
                if self.channel_update_needed(current_channel, new_channel):
                    batch[application_name] = ChannelUpdate(
                        channel=new_channel,
                        expected_status=expect_wls,
                    )
                else:
                    LOG.debug(f"{application_name} no channel upgrade needed")
            run_sync(self.jhelper.update_applications_channel(model, batch))

    def terraform_sync_channel_updates(self, application_data):
        for application_name, config in application_data.items():
            if config.get("tfvars_channel_var"):
                self.terraform_sync(
                    self.plugin.get_tfvar_config_key(),
                    {config["tfvars_channel_var"]: config["channel"]},
                )

    def run(self, status: Optional[Status] = None) -> Result:
        """Run plugin upgrade."""
        self.upgrade_charms(self.plugin.k8s_application_data, "openstack")
        if self.upgrade_release:
            self.terraform_sync_channel_updates(self.plugin.k8s_application_data)
        self.upgrade_charms(self.plugin.machine_application_data, "controller")
        if self.upgrade_release:
            self.terraform_sync_channel_updates(self.plugin.machine_application_data)
        return Result(ResultType.COMPLETED)


class EnableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform"""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Enable OpenStack {plugin.name}",
            f"Enabling OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to deploy openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}
        tfvars.update(self.plugin.set_tfvars_on_enable())
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)

        try:
            self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.plugin.set_application_names()
        LOG.debug(f"Application monitored for readiness: {apps}")
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=APPLICATION_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class DisableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Generic step to disable OpenStack application using Terraform"""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        plugin: OpenStackControlPlanePlugin,
    ) -> None:
        """Constructor for the generic plan.

        :param tfhelper: Terraform helper pointing to terraform plan
        :param jhelper: Juju helper with loaded juju credentials
        :param plugin: Plugin that uses this plan to perform callbacks to
                       plugin.
        """
        super().__init__(
            f"Disable OpenStack {plugin.name}",
            f"Disabling OpenStack {plugin.name} application",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.plugin = plugin
        self.model = OPENSTACK_MODEL

    def run(self, status: Optional[Status] = None) -> Result:
        """Apply terraform configuration to remove openstack application"""
        config_key = self.plugin.get_tfvar_config_key()

        try:
            tfvars = read_config(self.client, config_key)
        except ConfigItemNotFoundException:
            tfvars = {}

        try:
            if self.plugin.tf_plan_location == TerraformPlanLocation.PLUGIN_REPO:
                # Just destroy the terraform plan
                self.tfhelper.destroy()
                delete_config(self.client, config_key)
            else:
                # Update terraform variables to disable the application
                tfvars.update(self.plugin.set_tfvars_on_disable())
                update_config(self.client, config_key, tfvars)
                self.tfhelper.write_tfvars(tfvars)
                self.tfhelper.apply()
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.plugin.set_application_names()
        LOG.debug(f"Application monitored for removal: {apps}")
        # TODO(hemanth): Check if apps are removed or not.

        return Result(ResultType.COMPLETED)
