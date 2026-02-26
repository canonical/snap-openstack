# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import tenacity
from rich.status import Status
from snaphelpers import Snap, UnknownConfigKey

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    NodeNotExistInClusterException,
)
from sunbeam.core import ovn
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    convert_retry_failure_as_result,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.steps import DeployMachineApplicationStep, RemoveMachineUnitsStep
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.steps.configure import get_external_network_configs

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicroovnPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroovn"
APPLICATION = "microovn"
MICROOVN_APP_TIMEOUT = 1200
MICROOVN_UNIT_TIMEOUT = 1200
AGENT_APP = "openstack-network-agents"


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
        ovn_manager: ovn.OvnManager,
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
            list(ovn_manager.get_roles_for_microovn()),
            "Deploy MicroOVN",
            "Deploying MicroOVN",
        )
        self.openstack_model = OPENSTACK_MODEL
        self.ovn_manager = ovn_manager

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

        machine_ids = self.ovn_manager.get_machines()
        if machine_ids:
            extra_tfvars["microovn_machine_ids"] = machine_ids
            extra_tfvars["token_distributor_machine_ids"] = machine_ids[:1]

        extra_tfvars.update(
            {
                "endpoint_bindings": [
                    {"space": self.deployment.get_space(Networks.MANAGEMENT)},
                    {
                        "endpoint": "cluster",
                        "space": self.deployment.get_space(Networks.MANAGEMENT),
                    },
                    {
                        "endpoint": "certificates",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "ovsdb-external",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                    {
                        "endpoint": "ovsdb",
                        "space": self.deployment.get_space(Networks.INTERNAL),
                    },
                ]
            }
        )
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


class ReapplyMicroOVNTerraformPlanStep(BaseStep):
    """Reapply MicroOVN terraform plan."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        ovn_manager: ovn.OvnManager,
        extra_tfvars: dict | None = None,
    ):
        super().__init__(
            "Reapply MicroOVN Terraform plan",
            "Reapply MicroOVN Terraform plan",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = model
        self.ovn_manager = ovn_manager
        self.extra_tfvars = extra_tfvars or {}

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        for role in self.ovn_manager.get_roles_for_microovn():
            if self.client.cluster.list_nodes_by_role(role.name.lower()):
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=convert_retry_failure_as_result,
    )
    def run(self, status: Status | None = None) -> Result:
        """Apply terraform configuration to deploy MicroOVN."""
        # Apply Network configs everytime reapply is called
        network_configs = get_external_network_configs(self.client)
        if "charm_openstack_network_agents_config" not in self.extra_tfvars:
            self.extra_tfvars["charm_openstack_network_agents_config"] = {}

        if network_configs:
            LOG.debug(
                "Add external network configs from DemoSetup to extra tfvars: "
                f"{network_configs}"
            )
            self.extra_tfvars["charm_openstack_network_agents_config"].update(
                network_configs
            )

        statuses = ["active", "unknown"]
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=self.extra_tfvars,
            )
        except TerraformException as e:
            return Result(ResultType.FAILED, str(e))
        try:
            self.jhelper.wait_application_ready(
                APPLICATION,
                self.model,
                accepted_status=statuses,
                timeout=MICROOVN_UNIT_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


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


class SetOvnProviderStep(BaseStep):
    """Set OVN provider in the deployment configuration."""

    def __init__(self, client: Client, snap: Snap):
        super().__init__(
            "Set OVN provider",
            "Setting OVN provider in deployment configuration",
        )
        self.client = client
        self.snap = snap
        self.wanted_provider: ovn.OvnProvider | None = None

    def get_config_from_snap(self, snap: Snap) -> ovn.OvnProvider:
        """Get OVN provider from snap configuration.

        :param snap: the snap instance
        :return: the OVN provider
        """
        try:
            if snap.config.get(ovn.SNAP_PROVIDER_CONFIG_KEY):
                return ovn.OvnProvider.MICROOVN
        except UnknownConfigKey:
            # fallback to default
            pass
        return ovn.DEFAULT_PROVIDER

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            snap_value = self.get_config_from_snap(self.snap)
        except ValueError as e:
            return Result(
                ResultType.FAILED,
                str(e),
            )

        config = ovn.load_provider_config(self.client)
        configured_provider = config.provider
        if configured_provider == snap_value:
            LOG.debug(
                "OVN provider is already set to %s in deployment configuration",
                snap_value,
            )
            return Result(ResultType.SKIPPED)

        already_bootstrapped = self.client.cluster.check_sunbeam_bootstrapped()
        if already_bootstrapped and configured_provider != snap_value:
            LOG.debug(
                "OVN provider change detected after bootstrap, which is not supported"
            )
            return Result(ResultType.FAILED, "Changing OVN provider is not supported.")
        self.wanted_provider = snap_value
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Set OVN provider in deployment configuration to the desired provider."""
        if self.wanted_provider is None:
            return Result(ResultType.FAILED, "Invalid state, wanted_provider is None")
        config = ovn.load_provider_config(self.client)
        config.provider = self.wanted_provider
        ovn.write_provider_config(self.client, config)
        return Result(ResultType.COMPLETED)
