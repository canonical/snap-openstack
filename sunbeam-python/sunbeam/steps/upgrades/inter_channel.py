# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    run_plan,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.steps.cinder_volume import CONFIG_KEY as CINDER_VOLUME_CONFIG_KEY
from sunbeam.steps.hypervisor import CONFIG_KEY as HYPERVISOR_CONFIG_KEY
from sunbeam.steps.k8s import K8S_CONFIG_KEY
from sunbeam.steps.microceph import CONFIG_KEY as MICROCEPH_CONFIG_KEY
from sunbeam.steps.openstack import CONFIG_KEY as OPENSTACK_CONFIG_KEY
from sunbeam.steps.openstack import OPENSTACK_DEPLOY_TIMEOUT
from sunbeam.steps.sunbeam_machine import CONFIG_KEY as SUNBEAM_MACHINE_CONFIG_KEY
from sunbeam.steps.upgrades.base import UpgradeCoordinator, UpgradeFeatures
from sunbeam.versions import (
    MISC_CHARMS_K8S,
    MYSQL_CHARMS_K8S,
    OPENSTACK_CHARMS_K8S,
    OVN_CHARMS_K8S,
)

LOG = logging.getLogger(__name__)
console = Console()


class BaseUpgrade(BaseStep, JujuStepHelper):
    def __init__(
        self,
        name: str,
        description: str,
        client: Client,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of BaseUpgrade class.

        :client: Client for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(name, description)
        self.client = client
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model

    def run(self, status: Status | None = None) -> Result:
        """Run control plane and machine charm upgrade."""
        result = self.pre_upgrade_tasks(status)
        if result.result_type == ResultType.FAILED:
            return result

        self.upgrade_tasks(status)
        if result.result_type == ResultType.FAILED:
            return result

        result = self.post_upgrade_tasks(status)
        return result

    def upgrade_tasks(self, status: Status | None = None) -> Result:
        """Perform the upgrade tasks."""
        return Result(ResultType.COMPLETED)

    def pre_upgrade_tasks(self, status: Status | None = None) -> Result:
        """Tasks to run before the upgrade."""
        return Result(ResultType.COMPLETED)

    def post_upgrade_tasks(self, status: Status | None = None) -> Result:
        """Tasks to run after the upgrade."""
        return Result(ResultType.COMPLETED)

    def upgrade_applications(
        self,
        apps: list[str],
        charms: list[str],
        model: str,
        tfhelper: TerraformHelper,
        config: str,
        timeout: int,
        status: Status | None = None,
    ) -> Result:
        """Upgrade applications.

        :param apps: List of applications to be upgraded
        :param charms: List of charms
        :param model: Name of model
        :param tfhelper: Tfhelper of associated plan
        :param config: Terraform config key used to store config in clusterdb
        :param timeout: Timeout to wait for apps in expected status
        :param status: Status object to update charm status
        """
        expected_wls = ["active", "blocked", "unknown"]
        LOG.debug(
            f"Upgrading applications using terraform plan {tfhelper.plan}: {apps}"
        )
        try:
            tfhelper.update_partial_tfvars_and_apply_tf(
                self.client, self.manifest, charms, config
            )
        except TerraformException as e:
            LOG.exception("Error upgrading cloud")
            return Result(ResultType.FAILED, str(e))
        status_queue: queue.Queue[str] = queue.Queue(maxsize=len(apps))
        task = update_status_background(self, apps, status_queue, status)
        try:
            self.jhelper.wait_until_desired_status(
                model,
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


class UpgradeControlPlane(BaseUpgrade):
    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of BaseUpgrade class.

        :client: Client for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade Openstack charms",
            "Upgrading Openstack charms",
            client,
            jhelper,
            manifest,
            model,
        )
        self.deployment = deployment
        self.tfhelper = tfhelper
        self.config = OPENSTACK_CONFIG_KEY

    def upgrade_tasks(self, status: Status | None = None) -> Result:
        """Perform the upgrade tasks."""
        # Step 1: Upgrade mysql charms
        LOG.debug("Upgrading Mysql charms")
        charms = list(MYSQL_CHARMS_K8S.keys())
        apps = self.get_apps_filter_by_charms(self.model, charms)
        result = self.upgrade_applications(
            apps, charms, self.model, self.tfhelper, self.config, 1200, status
        )
        if result.result_type == ResultType.FAILED:
            return result

        # Step 2: Upgrade all openstack core charms
        LOG.debug("Upgrading openstack core charms")
        charms = (
            list(MISC_CHARMS_K8S.keys())
            + list(OVN_CHARMS_K8S.keys())
            + list(OPENSTACK_CHARMS_K8S.keys())
        )
        apps = self.get_apps_filter_by_charms(self.model, charms)
        result = self.upgrade_applications(
            apps,
            charms,
            self.model,
            self.tfhelper,
            self.config,
            OPENSTACK_DEPLOY_TIMEOUT,
            status,
        )
        if result.result_type == ResultType.FAILED:
            return result

        # Step 3: Upgrade all features that uses openstack-plan
        LOG.debug("Upgrading openstack features that are enabled")
        # TODO(gboutry): We have a loaded manifest, can't we get charms from there ?
        charms = (
            self.deployment.get_feature_manager().get_all_charms_in_openstack_plan()
        )
        apps = self.get_apps_filter_by_charms(self.model, charms)
        result = self.upgrade_applications(
            apps,
            charms,
            self.model,
            self.tfhelper,
            self.config,
            OPENSTACK_DEPLOY_TIMEOUT,
            status,
        )
        return result


class UpgradeMachineCharm(BaseUpgrade):
    def __init__(
        self,
        name: str,
        description: str,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        charms: list,
        config: str,
        timeout: int,
    ):
        """Create instance of BaseUpgrade class.

        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        :charms: List of charms to upgrade
        :tfplan: Terraform plan to reapply
        :config: Config key used to save tfvars in clusterdb
        :timeout: Time to wait for apps to come to desired status
        """
        super().__init__(
            name,
            description,
            client,
            jhelper,
            manifest,
            model,
        )
        self.charms = charms
        self.tfhelper = tfhelper
        self.config = config
        self.timeout = timeout

    def upgrade_tasks(self, status: Status | None = None) -> Result:
        """Perform the upgrade tasks."""
        apps = self.get_apps_filter_by_charms(self.model, self.charms)
        result = self.upgrade_applications(
            apps,
            self.charms,
            self.model,
            self.tfhelper,
            self.config,
            self.timeout,
            status,
        )

        return result


class UpgradeMicrocephCharm(UpgradeMachineCharm):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of UpgradeMicrocephCharm class.

        :client: Client to connect to clusterdb
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade Microceph charm",
            "Upgrading microceph charm",
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            ["microceph"],
            MICROCEPH_CONFIG_KEY,
            1200,
        )


class UpgradeCinderVolumeCharm(UpgradeMachineCharm):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of UpgradeCinderVolumeCharm class.

        :client: Client to connect to clusterdb
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade Cinder Volume charm",
            "Upgrading cinder-volume charm",
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            ["cinder-volume", "cinder-volume-ceph"],
            CINDER_VOLUME_CONFIG_KEY,
            1200,
        )


class UpgradeK8SCharm(UpgradeMachineCharm):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of UpgradeK8SCharm class.

        :client: Client to connect to clusterdb
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade K8S charm",
            "Upgrading K8S charm",
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            ["k8s"],
            K8S_CONFIG_KEY,
            1200,
        )


class UpgradeOpenstackHypervisorCharm(UpgradeMachineCharm):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of UpgradeOpenstackHypervisorCharm class.

        :client: Client to connect to clusterdb
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade hypervisor charm",
            "Upgrading hypervisor charm",
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            ["openstack-hypervisor"],
            HYPERVISOR_CONFIG_KEY,
            1200,
        )


class UpgradeSunbeamMachineCharm(UpgradeMachineCharm):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        """Create instance of UpgradeSunbeamMachineCharm class.

        :client: Client to connect to clusterdb
        :jhelper: Helper for interacting with pylibjuju
        :manifest: Manifest object
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade sunbeam-machine charm",
            "Upgrading sunbeam-machine charm",
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            ["sunbeam-machine"],
            SUNBEAM_MACHINE_CONFIG_KEY,
            1200,
        )


class ChannelUpgradeCoordinator(UpgradeCoordinator):
    def get_plan(self) -> list[BaseStep]:
        """Return the plan for this upgrade.

        Return the steps to complete this upgrade.
        """
        plan: list[BaseStep] = [UpgradeFeatures(self.deployment, upgrade_release=True)]
        return plan

        # --release-upgrade implementation is not proper and so the
        # option is hidden. Bug [1] requires to support the flag,
        # however to avoid any upgrades on other charms, this is
        # supported only for charms involved in observability
        # and rest of the code is commented out.
        # https://bugs.launchpad.net/snap-openstack/+bug/2115169
        """
        get_tf = self.deployment.get_tfhelper
        plan: list[BaseStep] = [
            UpgradeControlPlane(
                self.deployment,
                self.client,
                get_tf("openstack-plan"),
                self.jhelper,
                self.manifest,
                OPENSTACK_MODEL,
            ),
            UpgradeMicrocephCharm(
                self.client,
                get_tf("microceph-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
            UpgradeCinderVolumeCharm(
                self.client,
                get_tf("cinder-volume-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
        ]
        plan.append(
            UpgradeK8SCharm(
                self.client,
                get_tf("k8s-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            )
        )

        plan.extend(
            [
                UpgradeOpenstackHypervisorCharm(
                    self.client,
                    get_tf("hypervisor-plan"),
                    self.jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
                UpgradeSunbeamMachineCharm(
                    self.client,
                    get_tf("sunbeam-machine-plan"),
                    self.jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
                UpgradeFeatures(self.deployment, upgrade_release=True),
            ]
        )
        """

    def run_plan(self, show_hints: bool = False) -> None:
        """Execute the upgrade plan."""
        plan = self.get_plan()
        run_plan(plan, console, show_hints)
