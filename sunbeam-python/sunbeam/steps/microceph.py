# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ast
import logging
from typing import Any

import tenacity
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core import questions
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    SunbeamException,
    read_config,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    LeaderNotFoundException,
    UnitNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import DEFAULT_REGION, REGION_CONFIG_KEY
from sunbeam.core.steps import (
    AddMachineUnitsStep,
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException, TerraformHelper

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicrocephPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroceph"
APPLICATION = "microceph"
# Timeout set to 20 minutes instead of 9 minutes due to bug
# https://github.com/canonical/charm-microceph/issues/113
MICROCEPH_APP_TIMEOUT = 1200  # updating rgw configs can take some time
MICROCEPH_UNIT_TIMEOUT = (
    1200  # 20 minutes, adding / removing units can take a long time
)


def microceph_questions():
    return {
        "osd_devices": questions.PromptQuestion(
            "Ceph devices",
            description=(
                "Comma separated list of devices to be used by Ceph OSDs."
                " `/dev/disk/by-id/<id>` are preferred, as they are stable"
                " given the same device."
            ),
        ),
    }


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(min=2, max=10),
    retry=tenacity.retry_if_exception_type(ActionFailedException),
)
def list_disks(jhelper: JujuHelper, model: str, unit: str) -> tuple[dict, dict]:
    """Call list-disks action on an unit."""
    LOG.debug("Running list-disks on : %r", unit)
    action_result = jhelper.run_action(
        unit, model, "list-disks", action_params={"host-only": True}
    )
    LOG.debug(
        "Result after running action list-disks on %r: %r",
        unit,
        action_result,
    )
    osds = ast.literal_eval(action_result.get("osds", "[]"))
    unpartitioned_disks = ast.literal_eval(
        action_result.get("unpartitioned-disks", "[]")
    )
    return osds, unpartitioned_disks


def ceph_replica_scale(storage_nodes: int) -> int:
    return min(storage_nodes or 1, 3)


class DeployMicrocephApplicationStep(DeployMachineApplicationStep):
    """Deploy Microceph application using Terraform."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        refresh: bool = False,
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
            "Deploy MicroCeph",
            "Deploying MicroCeph",
            refresh,
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return MICROCEPH_APP_TIMEOUT

    def extra_tfvars(self) -> dict:
        """Extra terraform vars to pass to terraform apply."""
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        tfvars: dict[str, Any] = {
            "endpoint_bindings": [
                {
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    # microcluster related space
                    "endpoint": "admin",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "peers",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    # internal activites for ceph services, heartbeat + replication
                    "endpoint": "cluster",
                    "space": self.deployment.get_space(Networks.STORAGE_CLUSTER),
                },
                {
                    # access to ceph services
                    "endpoint": "public",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
                {
                    # acess to ceph services for related applications
                    "endpoint": "ceph",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
                # both mds and radosgw are specialized clients to access ceph services
                # they will not be used by sunbeam,
                # set them the same as other ceph clients
                {
                    "endpoint": "mds",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
                {
                    "endpoint": "radosgw",
                    "space": self.deployment.get_space(Networks.STORAGE),
                },
            ],
            "charm_microceph_config": {
                "enable-rgw": "*",
                "namespace-projects": True,
                "default-pool-size": ceph_replica_scale(len(storage_nodes)),
                "region": read_config(self.client, REGION_CONFIG_KEY).get(
                    "region", DEFAULT_REGION
                ),
            },
        }

        if len(storage_nodes):
            openstack_tfhelper = self.deployment.get_tfhelper("openstack-plan")
            openstack_tf_output = openstack_tfhelper.output()

            # Retreiving terraform state for non-existing plan using
            # data.terraform_remote_state errros out with message "No stored state
            # was found for the given workspace in the given backend".
            # It is not possible to try/catch this error, see
            # https://github.com/hashicorp/terraform-provider-google/issues/11035
            # The Offer URLs are retrieved by running terraform output on
            # openstack plan and pass them as variables.
            keystone_endpoints_offer_url = openstack_tf_output.get(
                "keystone-endpoints-offer-url"
            )
            cert_distributor_offer_url = openstack_tf_output.get(
                "cert-distributor-offer-url"
            )
            traefik_rgw_offer_url = openstack_tf_output.get("ingress-rgw-offer-url")

            if keystone_endpoints_offer_url:
                tfvars["keystone-endpoints-offer-url"] = keystone_endpoints_offer_url

            if cert_distributor_offer_url:
                tfvars["cert-distributor-offer-url"] = cert_distributor_offer_url

            if traefik_rgw_offer_url:
                tfvars["ingress-rgw-offer-url"] = traefik_rgw_offer_url

        return tfvars


class AddMicrocephUnitsStep(AddMachineUnitsStep):
    """Add Microceph Unit."""

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
    ):
        super().__init__(
            client,
            names,
            jhelper,
            CONFIG_KEY,
            APPLICATION,
            model,
            "Add MicroCeph unit",
            "Adding MicroCeph unit to machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return MICROCEPH_UNIT_TIMEOUT


class RemoveMicrocephUnitsStep(RemoveMachineUnitsStep):
    """Remove Microceph Unit."""

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
            "Remove MicroCeph unit(s)",
            "Removing MicroCeph unit(s) from machine",
        )

    def get_unit_timeout(self) -> int:
        """Return unit timeout in seconds."""
        return MICROCEPH_UNIT_TIMEOUT


class ConfigureMicrocephOSDStep(BaseStep):
    """Configure Microceph OSD disks."""

    _CONFIG = CONFIG_DISKS_KEY

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Configure MicroCeph storage", "Configuring MicroCeph storage")
        self.client = client
        self.node_name = name
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}
        self.machine_id = ""
        self.disks = ""
        self.unpartitioned_disks: list[str] = []
        self.osd_disks: list[str] = []

    def microceph_config_questions(self):
        """Return questions for configuring microceph."""
        disks_str = None
        if len(self.unpartitioned_disks) > 0:
            disks_str = ",".join(self.unpartitioned_disks)

        questions = microceph_questions()
        # Specialise question with local disk information.
        questions["osd_devices"].default_value = disks_str
        return questions

    def get_all_disks(self) -> None:
        """Get all disks from microceph unit."""
        try:
            node = self.client.cluster.get_node_info(self.node_name)
            self.machine_id = str(node.get("machineid"))
            unit = self.jhelper.get_unit_from_machine(
                APPLICATION, self.machine_id, self.model
            )
            osd_disks_dict, unpartitioned_disks_dict = list_disks(
                self.jhelper, self.model, unit
            )
            self.unpartitioned_disks = [
                disk.get("path") for disk in unpartitioned_disks_dict
            ]
            self.osd_disks = [disk.get("path") for disk in osd_disks_dict]
            LOG.debug(f"Unpartitioned disks: {self.unpartitioned_disks}")
            LOG.debug(f"OSD disks: {self.osd_disks}")

        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(str(e))
            raise SunbeamException("Unable to list disks")

    def prompt(
        self,
        console: Console | None = None,
        display_question_description: bool = False,
    ) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.get_all_disks()
        self.variables = questions.load_answers(self.client, self._CONFIG)
        self.variables.setdefault("microceph_config", {})
        self.variables["microceph_config"].setdefault(
            self.node_name, {"osd_devices": None}
        )

        # Set defaults
        if self.manifest and self.manifest.core.config.microceph_config:
            microceph_config = self.manifest.core.config.model_dump(by_alias=True)[
                "microceph_config"
            ]
        else:
            microceph_config = {}
        microceph_config.setdefault(self.node_name, {"osd_devices": None})

        # Preseed can have osd_devices as list. If so, change to comma separated str
        osd_devices = microceph_config.get(self.node_name, {}).get("osd_devices")
        if isinstance(osd_devices, list):
            osd_devices_str = ",".join(osd_devices)
            microceph_config[self.node_name]["osd_devices"] = osd_devices_str

        microceph_config_bank = questions.QuestionBank(
            questions=self.microceph_config_questions(),
            console=console,  # type: ignore
            preseed=microceph_config.get(self.node_name),
            previous_answers=self.variables.get("microceph_config", {}).get(
                self.node_name
            ),
            accept_defaults=self.accept_defaults,
            show_hint=display_question_description,
        )
        # Microceph configuration
        self.disks = microceph_config_bank.osd_devices.ask()
        self.variables["microceph_config"][self.node_name]["osd_devices"] = self.disks

        LOG.debug(self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.disks:
            LOG.debug(
                "Skipping ConfigureMicrocephOSDStep as no osd devices are selected"
            )
            return Result(ResultType.SKIPPED)

        # Remove any disks that are already added
        disks_to_add = set(self.disks.split(",")).difference(self.osd_disks)
        self.disks = ",".join(disks_to_add)
        if not self.disks:
            LOG.debug("Skipping ConfigureMicrocephOSDStep as devices are already added")
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Configure local disks on microceph."""
        failed = False
        try:
            unit = self.jhelper.get_unit_from_machine(
                APPLICATION, self.machine_id, self.model
            )
            LOG.debug(f"Running action add-osd on {unit}")
            action_result = self.jhelper.run_action(
                unit,
                self.model,
                "add-osd",
                action_params={
                    "device-id": self.disks,
                },
            )
            LOG.debug(f"Result after running action add-osd: {action_result}")
        except UnitNotFoundException as e:
            message = f"Microceph Adding disks {self.disks} failed: {str(e)}"
            failed = True
        except ActionFailedException as e:
            message = f"Microceph Adding disks {self.disks} failed: {str(e)}"
            LOG.debug(message)
            try:
                error = ast.literal_eval(str(e))
                results = ast.literal_eval(error.get("result"))
                for result in results:
                    if result.get("status") == "failure":
                        # disk already added to microceph, ignore the error
                        if "entry already exists" in result.get("message"):
                            disk = result.get("spec")
                            LOG.debug(f"Disk {disk} already added")
                            continue
                        else:
                            failed = True
            except Exception as ex:
                LOG.debug(f"Exception in eval action output: {str(ex)}")
                return Result(ResultType.FAILED, message)

        if failed:
            return Result(ResultType.FAILED, message)

        return Result(ResultType.COMPLETED)


class SetCephMgrPoolSizeStep(BaseStep):
    """Configure Microceph pool size for mgr."""

    def __init__(self, client: Client, jhelper: JujuHelper, model: str):
        super().__init__(
            "Set Microceph mgr Pool size",
            "Setting Microceph mgr pool size",
        )
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.storage_nodes: list[dict] = []

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        if len(self.storage_nodes):
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Set ceph mgr pool size."""
        try:
            pools = [
                ".mgr",
                ".rgw.root",
                "default.rgw.log",
                "default.rgw.control",
                "default.rgw.meta",
            ]
            unit = self.jhelper.get_leader_unit(APPLICATION, self.model)
            action_params = {
                "pools": ",".join(pools),
                "size": ceph_replica_scale(len(self.storage_nodes)),
            }
            LOG.debug(
                f"Running microceph action set-pool-size with params {action_params}"
            )
            result = self.jhelper.run_action(
                unit, self.model, "set-pool-size", action_params
            )
            if result.get("status") is None:
                return Result(
                    ResultType.FAILED,
                    f"ERROR: Failed to update pool size for {pools}",
                )
        except (
            ApplicationNotFoundException,
            LeaderNotFoundException,
            ActionFailedException,
        ) as e:
            LOG.debug(f"Failed to update pool size for {pools}", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class CheckMicrocephDistributionStep(BaseStep):
    _APPLICATION = APPLICATION

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        force: bool = False,
    ):
        super().__init__(
            "Check microceph distribution",
            "Check if node is hosting units of microceph",
        )
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model
        self.force = force

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(ResultType.FAILED, f"Node {self.node} not found in cluster")
        if Role.STORAGE.name.lower() not in node_info.get("role", ""):
            LOG.debug("Node %s is not a storage node", self.node)
            return Result(ResultType.SKIPPED)
        try:
            app = self.jhelper.get_application(self._APPLICATION, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {self._APPLICATION} has not been deployed yet",
            )

        for unit_name, unit in app.units.items():
            if unit.machine == str(node_info.get("machineid")):
                LOG.debug("Unit %s is running on node %s", unit_name, self.node)
                break
        else:
            LOG.debug("No %s units found on %s", self._APPLICATION, self.node)
            return Result(ResultType.SKIPPED)

        nb_storage_nodes = len(self.client.cluster.list_nodes_by_role("storage"))
        if nb_storage_nodes == 1 and not self.force:
            return Result(
                ResultType.FAILED,
                "Cannot remove the last storage node,"
                "--force to override, data loss will occur.",
            )

        replica_scale = ceph_replica_scale(nb_storage_nodes)

        if nb_storage_nodes - 1 < replica_scale and not self.force:
            return Result(
                ResultType.FAILED,
                "Cannot remove storage node, not enough storage nodes to maintain"
                f" replica scale {replica_scale}, --force to override",
            )

        return Result(ResultType.COMPLETED)


class DestroyMicrocephApplicationStep(DestroyMachineApplicationStep):
    """Destroy Microceph application using Terraform."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
    ):
        super().__init__(
            client,
            tfhelper,
            jhelper,
            manifest,
            CONFIG_KEY,
            [APPLICATION],
            model,
            "Destroy MicroCeph",
            "Destroying MicroCeph",
        )

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return MICROCEPH_APP_TIMEOUT

    def run(self, status: Status | None = None) -> Result:
        """Destroy microceph application."""
        # note(gboutry):this is a workaround for
        # https://github.com/juju/terraform-provider-juju/issues/473
        try:
            resources = self.tfhelper.state_list()
        except TerraformException as e:
            LOG.debug(f"Failed to list terraform state: {str(e)}")
            return Result(ResultType.FAILED, "Failed to list terraform state")

        for resource in resources:
            if "integration" in resource:
                try:
                    self.tfhelper.state_rm(resource)
                except TerraformException as e:
                    LOG.debug(f"Failed to remove resource {resource}: {str(e)}")
                    return Result(
                        ResultType.FAILED,
                        f"Failed to remove resource {resource} from state",
                    )

        return super().run(status)
