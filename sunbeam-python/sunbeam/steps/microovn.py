# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    NodeNotExistInClusterException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.steps import DeployMachineApplicationStep, RemoveMachineUnitsStep
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


class RemoveMicroOVNUnitsStep(RemoveMachineUnitsStep):
    """Remove MicroOVN Unit."""

    def __init__(
        self, client: Client, names: list[str] | str, jhelper: JujuHelper, model: str
    ):
        super().__init__(
            client,
            names,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            model,
            "Remove MicroOVN unit",
            "Removing MicroOVN unit from machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return MICROOVN_UNIT_TIMEOUT


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
