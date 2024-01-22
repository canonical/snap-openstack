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

import logging
from typing import Callable, Dict, List, Optional, TypedDict, Union

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.terraform import TerraformHelper
from sunbeam.commands.upgrades.base import UpgradeCoordinator, UpgradePlugins
from sunbeam.commands.upgrades.intra_channel import LatestInChannel
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    read_config,
    run_plan,
    update_config,
)
from sunbeam.jobs.juju import ChannelUpdate, JujuHelper, run_sync
from sunbeam.versions import (
    CHARM_VERSIONS,
    MACHINE_SERVICES,
    MISC_SERVICES_K8S,
    OPENSTACK_SERVICES_K8S,
    OVN_SERVICES_K8S,
)

LOG = logging.getLogger(__name__)
console = Console()


class UpgradeStrategy(TypedDict):
    """A strategy for upgrading applications.

    The strategy is a list of dicts. Each dict consists of:
        {
            "upgrade_f": f,
            "applications": [apps]
        },

    upgrade_f is the function to be applied to each application (in
    parallel) to perform the upgrade.

    applications is a list of applications that can be upgraded in parallel.

    Currently only apps that are upgraded with the same function can be
    grouped together.
    """

    upgrade_f: Callable[[list[str], str], None]
    applications: list[str]


class BaseUpgrade(BaseStep, JujuStepHelper):
    def __init__(
        self,
        name: str,
        description: str,
        client: Client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        model: str,
    ):
        """Create instance of BaseUpgrade class.

        :client: Client for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :model: Name of model containing charms.
        """
        super().__init__(name, description)
        self.client = client
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.model = model

    def get_upgrade_strategy_steps(self) -> UpgradeStrategy:
        """Return a strategy for performing the upgrade."""

        raise NotImplementedError

    def run(self, status: Optional[Status] = None) -> Result:
        """Run control plane and machine charm upgrade."""
        self.pre_upgrade_tasks()
        for step in self.get_upgrade_strategy_steps():
            step["upgrade_f"](step["applications"], self.model)
        self.post_upgrade_tasks()
        return Result(ResultType.COMPLETED)

    def pre_upgrade_tasks(self) -> None:
        """Tasks to run before the upgrade."""
        return

    def post_upgrade_tasks(self) -> None:
        """Tasks to run after the upgrade."""
        return

    def upgrade_applications(
        self,
        application_list: List[str],
        model: str,
        expect_wls: Optional[Dict[str, list[str]]] = None,
    ) -> None:
        """Upgrade applications.

        :param application_list: List of applications to be upgraded
        :param model: Name of model
        :param expect_wls: The expected workload status after charm upgrade.
        """
        if not expect_wls:
            expect_wls = {"workload": ["blocked", "active"]}
        batch = {}
        for app_name in application_list:
            new_channel = self.get_new_channel(app_name, model)
            if new_channel:
                LOG.debug(f"Upgrade needed for {app_name}")
                batch[app_name] = ChannelUpdate(
                    channel=new_channel,
                    expected_status=expect_wls,
                )
            else:
                LOG.debug(f"{app_name} no channel upgrade needed")
        run_sync(self.jhelper.update_applications_channel(model, batch))

    def get_new_channel(self, application_name: str, model: str) -> Union[str, None]:
        """Check application to see if an upgrade is needed.

        Check application to see if an upgrade is needed. A 'None'
        returned indicates no upgrade is needed.

        :param application_name: Name of application
        :param model: Model application is in
        """
        new_channel = None
        current_channel = run_sync(
            self.jhelper.get_charm_channel(application_name, model)
        )
        new_channel = CHARM_VERSIONS.get(application_name)
        if current_channel and new_channel:
            if self.channel_update_needed(current_channel, new_channel):
                return new_channel
            else:
                return None
        else:
            # No current_channel indicates application is missing
            return new_channel

    def terraform_sync(self, config_key: str, tfvars_delta: dict) -> None:
        """Sync the running state back to the Terraform state file.

        :param config_key: The config key used to access the data in microcluster
        :param tfvars_delta: The delta of changes to be applied to the terraform
                             vars stored in microcluster.
        """
        tfvars = read_config(self.client, config_key)
        tfvars.update(tfvars_delta)
        update_config(self.client, config_key, tfvars)
        self.tfhelper.write_tfvars(tfvars)
        self.tfhelper.sync()


class UpgradeControlPlane(BaseUpgrade):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        model: str,
    ):
        """Create instance of BaseUpgrade class.

        :client: Client for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade K8S charms",
            "Upgrade K8S charms channels to align with snap",
            client,
            jhelper,
            tfhelper,
            model,
        )

    def get_upgrade_strategy_steps(self) -> List[Dict[str, Union[Callable, List]]]:
        """Return a strategy for performing the upgrade.

        Upgrade all control plane applications in parallel.
        """
        upgrade_strategy_steps = [
            UpgradeStrategy(
                upgrade_f=self.upgrade_applications,
                applications=list(MISC_SERVICES_K8S.keys())
                + list(OVN_SERVICES_K8S.keys())  # noqa
                + list(OPENSTACK_SERVICES_K8S.keys()),  # noqa
            ),
        ]
        return upgrade_strategy_steps

    def post_upgrade_tasks(self) -> None:
        """Update channels in terraform vars db."""
        tfvars_delta = {
            "openstack-channel": run_sync(
                self.jhelper.get_charm_channel("keystone", "openstack")
            ),
            "ovn-channel": run_sync(
                self.jhelper.get_charm_channel("ovn-central", "openstack")
            ),
            "rabbitmq-channel": run_sync(
                self.jhelper.get_charm_channel("rabbitmq", "openstack")
            ),
            "traefik-channel": run_sync(
                self.jhelper.get_charm_channel("traefik", "openstack")
            ),
        }
        self.terraform_sync("TerraformVarsOpenstack", tfvars_delta)


class UpgradeMachineCharms(BaseUpgrade):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        model: str,
    ):
        """Create instance of BaseUpgrade class.

        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :model: Name of model containing charms.
        """
        super().__init__(
            "Upgrade Machine charms",
            "Upgrade machine charms channels to align with snap",
            client,
            jhelper,
            tfhelper,
            model,
        )

    def get_upgrade_strategy_steps(self) -> List[Dict[str, Union[Callable, List]]]:
        """Return a strategy for performing the upgrade.

        Upgrade all machine applications in parallel.
        """
        upgrade_strategy_steps = [
            UpgradeStrategy(
                upgrade_f=self.upgrade_applications, applications=MACHINE_SERVICES
            ),
        ]
        return upgrade_strategy_steps

    def post_upgrade_tasks(self) -> None:
        """Update channels in terraform vars db."""
        self.terraform_sync(
            "TerraformVarsMicrocephPlan",
            {
                "microceph_channel": run_sync(
                    self.jhelper.get_charm_channel("microceph", "controller")
                )
            },
        )
        self.terraform_sync(
            "TerraformVarsSunbeamMachine",
            {
                "charm_channel": run_sync(
                    self.jhelper.get_charm_channel("sunbeam-machine", "controller")
                )
            },
        )
        self.terraform_sync(
            "TerraformVarsHypervisor",
            {
                "charm_channel": run_sync(
                    self.jhelper.get_charm_channel("openstack-hypervisor", "controller")
                )
            },
        )
        self.terraform_sync(
            "TerraformVarsMicrok8sAddons",
            {
                "microk8s_channel": run_sync(
                    self.jhelper.get_charm_channel("microk8s", "controller")
                )
            },
        )


class ChannelUpgradeCoordinator(UpgradeCoordinator):
    def __init__(self, client: Client, jhelper: JujuHelper, tfhelper: TerraformHelper):
        """Upgrade coordinator.

        Execute plan for conducting an upgrade.

        :client: Client for interacting with clusterd
        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        """
        self.client = client
        self.jhelper = jhelper
        self.tfhelper = tfhelper

    def get_plan(self) -> list[BaseStep]:
        """Return the plan for this upgrade.

        Return the steps to complete this upgrade.
        """
        plan = [
            ValidationCheck(self.jhelper, self.tfhelper),
            LatestInChannel(self.jhelper),
            UpgradeControlPlane(self.client, self.jhelper, self.tfhelper, "openstack"),
            UpgradeMachineCharms(
                self.client, self.jhelper, self.tfhelper, "controller"
            ),
            UpgradePlugins(self.jhelper, self.tfhelper, upgrade_release=True),
        ]
        return plan

    def run_plan(self) -> None:
        """Execute the upgrade plan."""
        plan = self.get_plan()
        run_plan(plan, console)


class ValidationCheck(BaseStep):
    def __init__(self, jhelper: JujuHelper, tfhelper: TerraformHelper):
        """Run validation on the deployment.

        Check whether the requested upgrade is possible.

        :jhelper: Helper for interacting with pylibjuju
        :tfhelper: Helper for interaction with Terraform
        :channel: OpenStack channel to upgrade charms to
        """
        super().__init__("Validation", "Running pre-upgrade validation")
        self.jhelper = jhelper
        self.tfhelper = tfhelper

    def run(self, status: Optional[Status] = None) -> Result:
        """Run validation check."""
        rabbit_channel = run_sync(
            self.jhelper.get_charm_channel("rabbitmq", "openstack")
        )
        if rabbit_channel.split("/")[0] == "3.9":
            return Result(
                ResultType.FAILED,
                (
                    "Pre-upgrade validation failed: Rabbit charm cannot be "
                    "upgraded from 3.9"
                ),
            )
        else:
            return Result(ResultType.COMPLETED)
