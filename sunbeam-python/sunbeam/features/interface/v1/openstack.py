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


import asyncio
import logging
from abc import abstractmethod
from enum import Enum
from pathlib import Path
from typing import TypedDict

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.openstack import (
    DATABASE_MAX_POOL_SIZE,
    OPENSTACK_MODEL,
    TOPOLOGY_KEY,
    compute_resources_for_service,
    get_database_resource_dict,
    get_database_tfvars,
    write_database_resource_dict,
)
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.jobs.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    delete_config,
    read_config,
    run_plan,
    update_status_background,
)
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    JujuWaitException,
    TimeoutException,
    run_sync,
)
from sunbeam.jobs.manifest import AddManifestStep, Manifest

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
    """Enum to define Terraform plan location.

    There are 2 choices - either in sunbeam-terraform repo or
    part of feature in etc/deploy-<feature name> directory.
    """

    SUNBEAM_TERRAFORM_REPO = 1
    FEATURE_REPO = 2


class OpenStackControlPlaneFeature(EnableDisableFeature):
    """Interface for features to manage OpenStack Control plane components.

    Features that manages OpenStack control plane components using terraform
    plans can use this interface.

    The terraform plans can be defined either in sunbeam-terraform repo or as part
    of the feature in specific directory etc/deploy-<feature name>.
    """

    _manifest: Manifest | None
    interface_version = Version("0.0.1")

    def __init__(
        self, name: str, deployment: Deployment, tf_plan_location: TerraformPlanLocation
    ) -> None:
        """Constructor for feature interface.

        :param name: Name of the feature
        :param tf_plan_location: Location where terraform plans are placed
        """
        super().__init__(name, deployment)
        self.app_name = self.name.capitalize()
        self.tf_plan_location = tf_plan_location

        # Based on terraform plan location, tfplan will be either
        # openstack or feature name
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            self.tfplan = f"{OPENSTACK_TERRAFORM_PLAN}-plan"
            self.tfplan_dir = f"deploy-{OPENSTACK_TERRAFORM_PLAN}"
        else:
            self.tfplan = f"{self.name}-plan"
            self.tfplan_dir = f"deploy-{self.name}"

        self.snap = Snap()
        self._manifest = None

    @property
    def manifest(self) -> Manifest:
        """Return the manifest."""
        if self._manifest:
            return self._manifest

        self._manifest = self.deployment.get_manifest(self.user_manifest)

        return self._manifest

    def is_openstack_control_plane(self) -> bool:
        """Is feature deploys openstack control plane.

        :returns: True if feature deploys openstack control plane, else False.
        """
        return True

    def get_terraform_openstack_plan_path(self) -> Path:
        """Return Terraform OpenStack plan location."""
        return self.get_terraform_plans_base_path() / "etc" / "deploy-openstack"

    def pre_checks(self) -> None:
        """Perform preflight checks before enabling the feature.

        Also copies terraform plans to required locations.
        """
        preflight_checks = []
        preflight_checks.append(VerifyBootstrappedCheck(self.deployment.get_client()))
        run_preflight_checks(preflight_checks, console)

    def pre_enable(self) -> None:
        """Handler to perform tasks before enabling the feature."""
        self.pre_checks()
        super().pre_enable()

    def run_enable_plans(self) -> None:
        """Run plans to enable feature."""
        tfhelper = self.deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(self.deployment.get_connected_controller())

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(
                AddManifestStep(self.deployment.get_client(), self.user_manifest)
            )
        plan.extend(
            [
                TerraformInitStep(self.deployment.get_tfhelper(self.tfplan)),
                EnableOpenStackApplicationStep(tfhelper, jhelper, self),
            ]
        )

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application enabled.")

    def pre_disable(self) -> None:
        """Handler to perform tasks before disabling the feature."""
        self.pre_checks()
        super().pre_disable()

    def run_disable_plans(self) -> None:
        """Run plans to disable the feature."""
        tfhelper = self.deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(self.deployment.get_connected_controller())
        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(tfhelper, jhelper, self),
        ]

        run_plan(plan, console)
        click.echo(f"OpenStack {self.name} application disabled.")

    def get_tfvar_config_key(self) -> str:
        """Returns Config key to save terraform vars.

        If the terraform plans are in sunbeam-terraform repo, use the config
        key defined by the plan DeployOpenStackControlPlane i.e.,
        TerraformVarsOpenstack.
        If the terraform plans are part of feature directory, use config key
        TerraformVars-<feature name>.
        """
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            return OPENSTACK_TERRAFORM_VARS
        else:
            return f"TerraformVars{self.app_name}"

    def get_database_topology(self) -> str:
        """Returns the database topology of the cluster."""
        # Database topology can be set only during bootstrap and cannot be changed.
        client = self.deployment.get_client()
        topology = read_config(client, TOPOLOGY_KEY)
        return topology["database"]

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service.

        Example:
        {
            "cinder": {
              "cinder-k8s": 4,
              "cinder-ceph-k8s": 4,
            }
        }
        """
        return {}

    def get_database_resource_tfvars(self, *, enable: bool) -> dict:
        """Return tfvars for configuring memory for database."""
        client = self.deployment.get_client()
        try:
            config = read_config(client, self.get_tfvar_config_key())
        except ConfigItemNotFoundException:
            config = {}
        database_processes = self.get_database_charm_processes()
        resource_dict = get_database_resource_dict(client)
        if enable:
            resource_dict.update(
                {
                    service: compute_resources_for_service(
                        connection, DATABASE_MAX_POOL_SIZE
                    )
                    for service, connection in database_processes.items()
                }
            )
        else:
            for service in database_processes:
                resource_dict.pop(service, None)
        write_database_resource_dict(client, resource_dict)
        return get_database_tfvars(
            config.get("many-mysql", False),
            resource_dict,
            config.get("os-api-scale", 1),
        )

    def set_application_timeout_on_enable(self) -> int:
        """Set Application Timeout on enabling the feature.

        The feature plan will timeout if the applications
        are not in active status within in this time.
        """
        return APPLICATION_DEPLOY_TIMEOUT

    def set_application_timeout_on_disable(self) -> int:
        """Set Application Timeout on disabling the feature.

        The feature plan will timeout if the applications
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
    def enable_feature(self) -> None:
        """Enable feature command."""
        super().enable_feature()

    @abstractmethod
    def disable_feature(self) -> None:
        """Disable feature command."""
        super().disable_feature()

    def add_horizon_plugin_to_tfvars(self, plugin: str) -> dict[str, list[str]]:
        """Tf vars to have the given plugin enabled.

        Return of the function is expected to be passed to set_tfvars_on_enable.
        """
        try:
            tfvars = read_config(
                self.deployment.get_client(),
                self.get_tfvar_config_key(),
            )
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
            tfvars = read_config(
                self.deployment.get_client(),
                self.get_tfvar_config_key(),
            )
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
        # Nothig to do if the plan is openstack-plan as it is taken
        # care during control plane refresh
        if (
            not upgrade_release
            or self.tf_plan_location  # noqa W503
            == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO  # noqa: W503
        ):
            LOG.debug(
                f"Ignore upgrade_hook for feature {self.name}, the corresponding apps"
                f" will be refreshed as part of Control plane refresh"
            )
            return

        tfhelper = self.deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(self.deployment.get_connected_controller())
        plan = [
            UpgradeOpenStackApplicationStep(tfhelper, jhelper, self, upgrade_release),
        ]

        run_plan(plan, console)


class UpgradeOpenStackApplicationStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
        upgrade_release: bool = False,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Refresh OpenStack {feature.name}",
            f"Refresh OpenStack {feature.name} application",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL
        self.upgrade_release = upgrade_release

    def run(self, status: Status | None = None) -> Result:
        """Run feature upgrade."""
        LOG.debug(f"Upgrading feature {self.feature.name}")
        expected_wls = ["active", "blocked", "unknown"]
        tfvar_map = self.feature.manifest_attributes_tfvar_map()
        charms = list(tfvar_map.get(self.feature.tfplan, {}).get("charms", {}).keys())
        apps = self.get_apps_filter_by_charms(self.model, charms)
        config = self.feature.get_tfvar_config_key()
        timeout = self.feature.set_application_timeout_on_enable()

        try:
            self.tfhelper.update_partial_tfvars_and_apply_tf(
                self.feature.deployment.get_client(),
                self.feature.manifest,
                charms,
                config,
            )
        except TerraformException as e:
            LOG.exception(f"Error upgrading feature {self.feature.name}")
            return Result(ResultType.FAILED, str(e))
        queue: asyncio.queues.Queue[str] = asyncio.queues.Queue(maxsize=len(apps))
        task = run_sync(update_status_background(self, apps, queue, status))
        try:
            run_sync(
                self.jhelper.wait_until_desired_status(
                    self.model,
                    apps,
                    expected_wls,
                    timeout=timeout,
                    queue=queue,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class EnableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Generic step to enable OpenStack application using Terraform."""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Enable OpenStack {feature.name}",
            f"Enabling OpenStack {feature.name} application",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        config_key = self.feature.get_tfvar_config_key()
        extra_tfvars = self.feature.set_tfvars_on_enable()
        extra_tfvars.update(self.feature.get_database_resource_tfvars(enable=True))

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.feature.deployment.get_client(),
                self.feature.manifest,
                tfvar_config=config_key,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.feature.set_application_names()
        LOG.debug(f"Application monitored for readiness: {apps}")
        queue: asyncio.queues.Queue[str] = asyncio.queues.Queue(maxsize=len(apps))
        task = run_sync(update_status_background(self, apps, queue, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=self.feature.set_application_timeout_on_enable(),
                    queue=queue,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class DisableOpenStackApplicationStep(BaseStep, JujuStepHelper):
    """Generic step to disable OpenStack application using Terraform."""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Disable OpenStack {feature.name}",
            f"Disabling OpenStack {feature.name} application",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to remove openstack application."""
        config_key = self.feature.get_tfvar_config_key()

        try:
            if self.feature.tf_plan_location == TerraformPlanLocation.FEATURE_REPO:
                # Just destroy the terraform plan
                self.tfhelper.destroy()
                delete_config(self.feature.deployment.get_client(), config_key)
            else:
                # Update terraform variables to disable the application
                extra_tfvars = self.feature.set_tfvars_on_disable()
                extra_tfvars.update(
                    self.feature.get_database_resource_tfvars(enable=False)
                )
                self.tfhelper.update_tfvars_and_apply_tf(
                    self.feature.deployment.get_client(),
                    self.feature.manifest,
                    tfvar_config=config_key,
                    override_tfvars=extra_tfvars,
                )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.feature.set_application_names()
        LOG.debug(f"Application monitored for removal: {apps}")
        try:
            run_sync(
                self.jhelper.wait_application_gone(
                    apps,
                    self.model,
                    timeout=self.feature.set_application_timeout_on_disable(),
                )
            )
        except TimeoutException as e:
            LOG.debug(f"Failed to destroy {apps}", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class WaitForApplicationsStep(BaseStep):
    """Wait for Applications to settle."""

    def __init__(self, jhelper: JujuHelper, apps: list, model: str, timeout: int = 300):
        super().__init__(
            "Wait for apps to settle", "Waiting for the applications to settle"
        )
        self.jhelper = jhelper
        self.apps = apps
        self.model = model
        self.timeout = timeout

    def run(self, status: Status | None = None) -> Result:
        """Wait for applications to be idle."""
        LOG.debug(f"Application monitored for readiness: {self.apps}")
        units = []
        accepted_unit_status = {"agent": ["idle"], "workload": ["active"]}
        try:
            for app in self.apps:
                try:
                    application = run_sync(
                        self.jhelper.get_application(app, self.model)
                    )
                    units.extend(application.units)
                except ApplicationNotFoundException:
                    # Ignore if the application is not found
                    LOG.debug(f"Application {app} not found")

            run_sync(
                self.jhelper.wait_units_ready(
                    units,
                    self.model,
                    accepted_status=accepted_unit_status,
                    timeout=self.timeout,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
