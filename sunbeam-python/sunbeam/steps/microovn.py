# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import traceback
import typing

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    read_config,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.openstack_api import remove_hypervisor
from sunbeam.core.steps import DeployMachineApplicationStep
from sunbeam.core.terraform import TerraformHelper
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import openstack
else:
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicroovnPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroovn"
APPLICATION = "microovn"
MICROOVN_APP_TIMEOUT = 1200
MICROOVN_UNIT_TIMEOUT = 1200


class DeployMicroOVNApplicationStep(DeployMachineApplicationStep):
    """Deploy MicroOVN application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            APPLICATION,
            model,
            [Role.NETWORK],
            "Deploy OpenStack microovn",
            "Deploying OpenStack microovn",
        )
        self.openstack_model = OPENSTACK_MODEL

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return MICROOVN_APP_TIMEOUT

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        openstack_tfhelper = self.deployment.get_tfhelper("openstack-plan")
        openstack_tf_output = openstack_tfhelper.output()

        juju_offers = {
            "ca-offer-url",
            "ovn-relay-offer-url",
        }
        extra_tfvars = {offer: openstack_tf_output.get(offer) for offer in juju_offers}

        nodes = self.client.cluster.list_nodes_by_role("network")
        machine_ids = {
            node.get("machineid") for node in nodes if node.get("machineid") != -1
        }
        if machine_ids:
            extra_tfvars["microovn_machine_ids"] = list(machine_ids)
            extra_tfvars["token_distributor_machine_ids"] = list(machine_ids)
        return extra_tfvars


class ReapplyMicroOVNOptionalIntegrationsStep(DeployMicroOVNApplicationStep):
    """Reapply MicroOVN optional integrations using Terraform."""

    def tf_apply_extra_args(self) -> list[str]:
        """Extra args for terraform apply to reapply only optional CMR integrations."""
        return [
            "-target=juju_integration.microovn-microcluster-token-distributor",
            "-target=juju_integration.microovn-certs",
            "-target=juju_integration.microovn-ovsdb-cms",
            "-target=juju_integration.microovn-openstack-network-agents",
        ]


class RemoveMicroOVNUnitsStep(BaseStep, JujuStepHelper):
    """Remove Microovn Unit."""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
        force: bool = False,
    ):
        super().__init__(
            "Remove MicroOVN unit",
            "Removing MicroOVN unit from machine",
        )
        self.client = client
        self.names = names if isinstance(names, list) else [names]
        self.node_name: str = self.names[0]
        self.jhelper = jhelper
        self.model = model
        self.force = force
        self.unit: str | None = None
        self.machine_id = ""

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.node_name)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug(f"Machine {self.node_name} does not exist, skipping.")
            return Result(ResultType.SKIPPED)

        try:
            application = self.jhelper.get_application(APPLICATION, self.model)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return Result(
                ResultType.SKIPPED, "MicroOVN application has not been deployed yet"
            )

        for unit_name, unit in application.units.items():
            if unit.machine == self.machine_id:
                LOG.debug(f"Unit {unit_name} is deployed on machine: {self.machine_id}")
                self.unit = unit_name
                break
        if not self.unit:
            LOG.debug(f"Unit is not deployed on machine: {self.machine_id}, skipping.")
            return Result(ResultType.SKIPPED)
        try:
            results = self.jhelper.run_action(self.unit, self.model, "running-guests")
        except ActionFailedException:
            LOG.debug("Failed to run action on microovn unit", exc_info=True)
            return Result(ResultType.FAILED, "Failed to run action on microovn unit")

        if result := results.get("result"):
            guests = json.loads(result)
            LOG.debug(f"Found guests on microovn: {guests}")
            if guests and not self.force:
                return Result(
                    ResultType.FAILED,
                    "Guests are running on microovn, aborting",
                )
        return Result(ResultType.COMPLETED)

    def remove_machine_id_from_tfvar(self) -> None:
        """Remove machine if from terraform vars saved in cluster db."""
        try:
            tfvars = read_config(self.client, CONFIG_KEY)
        except ConfigItemNotFoundException:
            tfvars = {}

        machine_ids = tfvars.get("microovn_machine_ids", [])
        if self.machine_id in machine_ids:
            machine_ids.remove(self.machine_id)
            tfvars.update({"microovn_machine_ids": machine_ids})
            update_config(self.client, CONFIG_KEY, tfvars)

    def run(self, status: Status | None = None) -> Result:
        """Remove unit from MicroOVN application on Juju model."""
        if not self.unit:
            return Result(ResultType.FAILED, "Unit not found on machine")
        try:
            self.jhelper.run_action(self.unit, self.model, "disable")
        except ActionFailedException as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, "Failed to disable MicroOVN unit")
        try:
            self.jhelper.remove_unit(APPLICATION, self.unit, self.model)
            self.remove_machine_id_from_tfvar()
            self.jhelper.wait_units_gone(
                [self.unit],
                self.model,
                timeout=MICROOVN_UNIT_TIMEOUT,
            )
            self.jhelper.wait_application_ready(
                APPLICATION,
                self.model,
                accepted_status=["active", "unknown"],
                timeout=MICROOVN_UNIT_TIMEOUT,
            )
        except (ApplicationNotFoundException, TimeoutError) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        try:
            remove_hypervisor(self.node_name, self.jhelper)
        except Exception as e:
            if openstack and isinstance(e, openstack.exceptions.SDKException):
                LOG.error(
                    "Encountered error removing microovn references from control plane."
                )
                if self.force:
                    LOG.warning("Force mode set, ignoring exception:")
                    traceback.print_exception(e)
                else:
                    return Result(ResultType.FAILED, str(e))
            else:
                # Re-raise if it's not an openstack SDK exception
                raise

        return Result(ResultType.COMPLETED)


class EnableMicroOVNStep(BaseStep, JujuStepHelper):
    """Enable MicroOVN service."""

    def __init__(
        self,
        client: Client,
        node: str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            "Enable MicroOVN service",
            "Enabling MicroOVN service for unit",
        )
        self.client = client
        self.node = node
        self.jhelper = jhelper
        self.model = model
        self.unit: str | None = None
        self.machine_id = ""

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node = self.client.cluster.get_node_info(self.node)
            self.machine_id = str(node.get("machineid"))
        except NodeNotExistInClusterException:
            LOG.debug(f"Machine {self.node} does not exist, skipping.")
            return Result(ResultType.SKIPPED)

        try:
            application = self.jhelper.get_application(APPLICATION, self.model)
        except ApplicationNotFoundException as e:
            LOG.debug(str(e))
            return Result(
                ResultType.SKIPPED, "microovn application has not been deployed yet"
            )

        for unit_name, unit in application.units.items():
            if unit.machine == self.machine_id:
                LOG.debug(f"Unit {unit_name} is deployed on machine: {self.machine_id}")
                self.unit = unit_name
                break
        if not self.unit:
            LOG.debug(f"Unit is not deployed on machine: {self.machine_id}, skipping.")
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Enable MicroOVN service on node."""
        if not self.unit:
            return Result(ResultType.FAILED, "Unit not found on machine")

        return Result(ResultType.COMPLETED)
