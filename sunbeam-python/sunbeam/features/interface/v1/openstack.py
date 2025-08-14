# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue
import typing
from abc import abstractmethod
from enum import Enum
from pathlib import Path

import click
from packaging.version import Version
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    delete_config,
    read_config,
    run_plan,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import AddManifestStep, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.base import ConfigType, EnableDisableFeature
from sunbeam.steps.openstack import (
    DATABASE_MAX_POOL_SIZE,
    DATABASE_STORAGE_KEY,
    TOPOLOGY_KEY,
    check_database_size_modifications_in_manifest,
    compute_resources_for_service,
    get_database_default_storage_dict,
    get_database_resource_dict,
    get_database_resource_tfvars,
    get_database_storage_dict,
    service_scale_function,
    write_database_resource_dict,
    write_database_storage_dict,
)

LOG = logging.getLogger(__name__)
console = Console()

APPLICATION_DEPLOY_TIMEOUT = 900  # 15 minutes
OPENSTACK_TERRAFORM_VARS = "TerraformVarsOpenstack"
OPENSTACK_TERRAFORM_PLAN = "openstack"


class TerraformPlanLocation(Enum):
    """Enum to define Terraform plan location.

    There are 2 choices - either in sunbeam-terraform repo or
    part of feature in etc/deploy-<feature name> directory.
    """

    SUNBEAM_TERRAFORM_REPO = 1
    FEATURE_REPO = 2


class OpenStackControlPlaneFeature(EnableDisableFeature, typing.Generic[ConfigType]):
    """Interface for features to manage OpenStack Control plane components.

    Features that manages OpenStack control plane components using terraform
    plans can use this interface.

    The terraform plans can be defined either in sunbeam-terraform repo or as part
    of the feature in specific directory etc/deploy-<feature name>.
    """

    _manifest: Manifest | None
    interface_version = Version("0.0.1")
    tf_plan_location: TerraformPlanLocation

    def __init__(self) -> None:
        """Constructor for feature interface.

        :param tf_plan_location: Location where terraform plans are placed
        """
        super().__init__()
        self.app_name = self.name.capitalize()

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

        manifest = click.get_current_context().obj.get_manifest(self.user_manifest)
        self._manifest = manifest

        return manifest

    def is_openstack_control_plane(self) -> bool:
        """Is feature deploys openstack control plane.

        :returns: True if feature deploys openstack control plane, else False.
        """
        return True

    def get_terraform_openstack_plan_path(self) -> Path:
        """Return Terraform OpenStack plan location."""
        return self.get_terraform_plans_base_path() / "etc" / "deploy-openstack"

    def pre_checks(self, deployment: Deployment) -> None:
        """Perform preflight checks before enabling the feature.

        Also copies terraform plans to required locations.
        """
        preflight_checks = []
        preflight_checks.append(VerifyBootstrappedCheck(deployment.get_client()))
        run_preflight_checks(preflight_checks, console)

    def pre_enable(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Handler to perform tasks before enabling the feature."""
        self.pre_checks(deployment)
        super().pre_enable(deployment, config, show_hints)

    def run_enable_plans(
        self, deployment: Deployment, config: ConfigType, show_hints: bool
    ) -> None:
        """Run plans to enable feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        plan: list[BaseStep] = []
        if self.user_manifest:
            plan.append(AddManifestStep(deployment.get_client(), self.user_manifest))
        plan.extend(
            [
                TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
                EnableOpenStackApplicationStep(
                    deployment, config, tfhelper, jhelper, self
                ),
            ]
        )

        run_plan(plan, console, show_hints)
        click.echo(f"OpenStack {self.display_name} application enabled.")

    def pre_disable(self, deployment: Deployment, show_hints: bool) -> None:
        """Handler to perform tasks before disabling the feature."""
        self.pre_checks(deployment)
        super().pre_disable(deployment, show_hints)

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Run plans to disable the feature."""
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            TerraformInitStep(tfhelper),
            DisableOpenStackApplicationStep(deployment, tfhelper, jhelper, self),
        ]

        run_plan(plan, console, show_hints)
        click.echo(f"OpenStack {self.display_name} application disabled.")

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

    def get_database_topology(self, deployment: Deployment) -> str:
        """Returns the database topology of the cluster."""
        # Database topology can be set only during bootstrap and cannot be changed.
        client = deployment.get_client()
        topology = read_config(client, TOPOLOGY_KEY)
        return topology["database"]

    def get_cluster_topology(self, deployment: Deployment) -> str:
        """Returns the cluster topology of the cluster."""
        # Topology can be set only during bootstrap and cannot be changed.
        client = deployment.get_client()
        topology = read_config(client, TOPOLOGY_KEY)
        return topology["topology"]

    def get_database_charm_processes(self) -> dict[str, dict[str, int]]:
        """Returns the database processes accessing this service.

        Example:
        {
            "cinder": {
              "cinder-k8s": 4,
              "cinder-volume": 4,
            }
        }
        """
        return {}

    def get_database_default_charm_storage(self) -> dict[str, str]:
        """Returns the database storage defaults for this service.

        The default storage is required at feature level only for
        multi-mysql deployments.

        Example:
        {
            "gnocchi": "10G",
            "ceilometer": "5G",
        }
        """
        return {}

    def _get_database_resource_tfvars(self, client: Client, *, enable: bool) -> dict:
        """Return tfvars for configuring memory for database."""
        try:
            config = read_config(client, self.get_tfvar_config_key())
        except ConfigItemNotFoundException:
            config = {}
        storage_nodes = client.cluster.list_nodes_by_role("storage")
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
        return get_database_resource_tfvars(
            config.get("many-mysql", False),
            resource_dict,
            service_scale_function(config.get("os-api-scale", 1), len(storage_nodes)),
        )

    def _get_database_storage_map_tfvars(
        self, deployment: Deployment, *, enable: bool
    ) -> dict:
        """Return tfvars for storage required for database."""
        many_mysql = self.get_database_topology(deployment) == "multi"
        client = deployment.get_client()

        # Nothing to do at feature level for single database
        # Just return the value from cluster database
        if not many_mysql:
            try:
                storage_dict = read_config(client, DATABASE_STORAGE_KEY)
            except ConfigItemNotFoundException:
                storage_dict = {}

            return {"mysql-storage": {"database": storage_dict.get("mysql")}}

        # Get default storage values from core and this feature
        storage_defaults = get_database_default_storage_dict(many_mysql)
        storage_defaults_for_charm = self.get_database_default_charm_storage()
        storage_defaults.update(storage_defaults_for_charm)

        # Get storage dict to apply
        storage_dict = get_database_storage_dict(
            client, many_mysql, self.manifest, storage_defaults
        )

        # Remove service from storage dict if feature is disabled
        if not enable:
            for service in storage_defaults_for_charm:
                storage_dict.pop(service, None)

        write_database_storage_dict(client, storage_dict)

        storage_map = {
            service: {"database": storage} for service, storage in storage_dict.items()
        }
        return {"mysql-storage-map": storage_map}

    def get_database_tfvars(self, deployment: Deployment, *, enable: bool) -> dict:
        """Return tfvars for database."""
        client = deployment.get_client()
        database_tfvars = self._get_database_resource_tfvars(client, enable=enable)
        database_tfvars.update(
            self._get_database_storage_map_tfvars(deployment, enable=enable)
        )
        return database_tfvars

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
    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan.

        Returns list of applications that are deployed by the
        terraform plan during enable. During disable, these
        applications should get destroyed.
        """

    @abstractmethod
    def set_tfvars_on_enable(self, deployment: Deployment, config: ConfigType) -> dict:
        """Set terraform variables to enable the application."""

    @abstractmethod
    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""

    @abstractmethod
    def set_tfvars_on_resize(self, deployment: Deployment, config: ConfigType) -> dict:
        """Set terraform variables to resize the application."""

    def add_horizon_plugin_to_tfvars(
        self, deployment: Deployment, plugin: str
    ) -> dict[str, list[str]]:
        """Tf vars to have the given plugin enabled.

        Return of the function is expected to be passed to set_tfvars_on_enable.
        """
        try:
            tfvars = read_config(
                deployment.get_client(),
                self.get_tfvar_config_key(),
            )
        except ConfigItemNotFoundException:
            tfvars = {}

        horizon_plugins = tfvars.get("horizon-plugins", [])
        if plugin not in horizon_plugins:
            horizon_plugins.append(plugin)

        return {"horizon-plugins": sorted(horizon_plugins)}

    def remove_horizon_plugin_from_tfvars(
        self, deployment: Deployment, plugin: str
    ) -> dict[str, list[str]]:
        """TF vars to have the given plugin disabled.

        Return of the function is expected to be passed to set_tfvars_on_disable.
        """
        try:
            tfvars = read_config(
                deployment.get_client(),
                self.get_tfvar_config_key(),
            )
        except ConfigItemNotFoundException:
            tfvars = {}

        horizon_plugins = tfvars.get("horizon-plugins", [])
        if plugin in horizon_plugins:
            horizon_plugins.remove(plugin)

        return {"horizon-plugins": sorted(horizon_plugins)}

    def upgrade_hook(
        self,
        deployment: Deployment,
        upgrade_release: bool = False,
        show_hints: bool = False,
    ):
        """Run upgrade.

        :param upgrade_release: Whether to upgrade release
        """
        if upgrade_release:
            LOG.debug(f"Release upgrade not supported for feature {self.name}")
            return

        # Nothig to do if the plan is openstack-plan as it is taken
        # care during control plane refresh
        if self.tf_plan_location == TerraformPlanLocation.SUNBEAM_TERRAFORM_REPO:
            LOG.debug(
                f"Ignore upgrade_hook for feature {self.name}, the corresponding apps"
                f" will be refreshed as part of Control plane refresh"
            )
            return

        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)
        plan = [
            UpgradeOpenStackApplicationStep(
                deployment, tfhelper, jhelper, self, upgrade_release
            ),
        ]

        run_plan(plan, console, show_hints)


class UpgradeOpenStackApplicationStep(BaseStep, JujuStepHelper):
    def __init__(
        self,
        deployment: Deployment,
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
        self.deployment = deployment
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
                self.deployment.get_client(),
                self.feature.manifest,
                charms,
                config,
            )
        except TerraformException as e:
            LOG.exception(f"Error upgrading feature {self.feature.name}")
            return Result(ResultType.FAILED, str(e))
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                apps,
                status=expected_wls,
                timeout=timeout,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class EnableOpenStackApplicationStep(
    BaseStep, JujuStepHelper, typing.Generic[ConfigType]
):
    """Generic step to enable OpenStack application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        config: ConfigType,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        feature: OpenStackControlPlaneFeature,
        app_desired_status: list[str] = ["active"],
    ) -> None:
        """Constructor for the generic plan.

        :param jhelper: Juju helper with loaded juju credentials
        :param feature: Feature that uses this plan to perform callbacks to
                       feature.
        """
        super().__init__(
            f"Enable OpenStack {feature.display_name}",
            f"Enabling OpenStack {feature.display_name} application",
        )
        self.deployment = deployment
        self.config = config
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.app_desired_status = app_desired_status
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        # Check for database size modifications in manifest
        try:
            many_mysql = self.feature.get_database_topology(self.deployment) == "multi"
            client = self.deployment.get_client()
            database_size_changed = check_database_size_modifications_in_manifest(
                client, self.feature.manifest, many_mysql
            )
            if database_size_changed:
                return Result(
                    ResultType.FAILED,
                    "Storage sizes are immutable and cannot be modified in manifest",
                )
        except ValueError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy openstack application."""
        config_key = self.feature.get_tfvar_config_key()
        extra_tfvars = self.feature.set_tfvars_on_enable(self.deployment, self.config)
        extra_tfvars.update(
            self.feature.get_database_tfvars(self.deployment, enable=True)
        )

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.feature.manifest,
                tfvar_config=config_key,
                override_tfvars=extra_tfvars,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.feature.set_application_names(self.deployment)
        LOG.debug(f"Application monitored for readiness: {apps}")
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                apps,
                status=self.app_desired_status,
                timeout=self.feature.set_application_timeout_on_enable(),
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()

        return Result(ResultType.COMPLETED)


class DisableOpenStackApplicationStep(
    BaseStep, JujuStepHelper, typing.Generic[ConfigType]
):
    """Generic step to disable OpenStack application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
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
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.feature = feature
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        # Check for database size modifications in manifest
        try:
            many_mysql = self.feature.get_database_topology(self.deployment) == "multi"
            client = self.deployment.get_client()
            database_size_changed = check_database_size_modifications_in_manifest(
                client, self.feature.manifest, many_mysql
            )
            if database_size_changed:
                return Result(
                    ResultType.FAILED,
                    "Storage sizes are immutable and cannot be modified in manifest",
                )
        except ValueError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to remove openstack application."""
        config_key = self.feature.get_tfvar_config_key()

        try:
            if self.feature.tf_plan_location == TerraformPlanLocation.FEATURE_REPO:
                # Just destroy the terraform plan
                self.tfhelper.destroy()
                delete_config(self.deployment.get_client(), config_key)
            else:
                # Update terraform variables to disable the application
                extra_tfvars = self.feature.set_tfvars_on_disable(self.deployment)
                extra_tfvars.update(
                    self.feature.get_database_tfvars(self.deployment, enable=False)
                )
                self.tfhelper.update_tfvars_and_apply_tf(
                    self.deployment.get_client(),
                    self.feature.manifest,
                    tfvar_config=config_key,
                    override_tfvars=extra_tfvars,
                )
        except (TerraformException, TerraformStateLockedException) as e:
            return Result(ResultType.FAILED, str(e))

        apps = self.feature.set_application_names(self.deployment)
        LOG.debug(f"Application monitored for removal: {apps}")
        try:
            self.jhelper.wait_application_gone(
                apps,
                self.model,
                timeout=self.feature.set_application_timeout_on_disable(),
            )
        except TimeoutError as e:
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
        status_queue: queue.Queue[str] = queue.Queue()
        task = update_status_background(self, self.apps, status_queue, status)
        try:
            self.jhelper.wait_until_desired_status(
                self.model,
                self.apps,
                status=["active"],
                agent_status=["idle"],
                timeout=self.timeout,
                queue=status_queue,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.debug("Failed to wait for apps to settle", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        finally:
            task.stop()
        return Result(ResultType.COMPLETED)
