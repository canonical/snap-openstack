# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ast
import json
import logging
from typing import Any

import tenacity
from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.core import questions
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    StepContext,
    SunbeamException,
    read_config,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    ExecFailedException,
    JujuHelper,
    LeaderNotFoundException,
    UnitNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import DEFAULT_REGION, REGION_CONFIG_KEY
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    DestroyMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException, TerraformHelper

LOG = logging.getLogger(__name__)
CONFIG_KEY = "TerraformVarsMicrocephPlan"
CONFIG_DISKS_KEY = "TerraformVarsMicroceph"
APPLICATION = "microceph"
CEPH_NFS_RELATION = "ceph-nfs"
NFS_OFFER_NAME = "microceph-ceph-nfs"
RGW_OFFER_NAME = "microceph-ceph-rgw"
MICROCEPH_APP_TIMEOUT = (
    1800  # 30 minutes, can trigger to deploy mutliple units in parallel
)
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
    LOG.debug("Running list-disks on: %r", unit)
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
        wait_for_readiness: bool = True,
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
            [Role.STORAGE],
            "Deploy MicroCeph",
            "Deploying MicroCeph",
            wait_for_readiness=wait_for_readiness,
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


class RemoveMicrocephOSDsStep(BaseStep):
    """Remove a node's MicroCeph OSDs before removing its unit."""

    _OSD_REMOVE_TIMEOUT = 1800
    _COMMAND_TIMEOUT = _OSD_REMOVE_TIMEOUT + 60

    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        force: bool = False,
    ):
        super().__init__(
            "Remove MicroCeph OSDs",
            "Removing MicroCeph OSDs",
        )
        self.client = client
        self.node = name
        self.jhelper = jhelper
        self.model = model
        self.force = force
        self.unit: str | None = None
        self._units: list[str] = []

    def _prepare(self) -> Result:
        """Find a unit that can run cleanup before the target is removed."""
        self.unit = None
        self._units = []
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            node_info = None

        machines = self.jhelper.get_machines(self.model)
        target_machine_ids: set[str] = set()
        if node_info is not None:
            machine_id = node_info.get("machineid")
            if machine_id is not None and str(machine_id) not in {"", "-1"}:
                target_machine_ids.add(str(machine_id))
        target_machine_ids.update(
            str(machine_id)
            for machine_id, machine in machines.items()
            if machine.hostname == self.node
        )
        try:
            app = self.jhelper.get_application(APPLICATION, self.model)
        except ApplicationNotFoundException:
            LOG.debug("Failed to get application", exc_info=True)
            return Result(
                ResultType.SKIPPED,
                f"Application {APPLICATION} has not been deployed yet",
            )

        units = app.units
        if not units:
            if self.force:
                return Result(ResultType.SKIPPED)
            return Result(
                ResultType.FAILED,
                f"No MicroCeph unit is available to clean up {self.node}",
            )

        known_machine_ids = {str(machine_id) for machine_id in machines}
        target_units: list[str] = []
        surviving_units: list[str] = []
        for unit_name, unit in units.items():
            machine_id = str(unit.machine)
            if machine_id in target_machine_ids:
                target_units.append(unit_name)
            elif machine_id in known_machine_ids:
                surviving_units.append(unit_name)

        if not (target_units or surviving_units):
            return Result(
                ResultType.FAILED,
                f"Unable to identify a MicroCeph unit for {self.node}",
            )

        def unit_sort_key(unit_name: str) -> tuple[bool, bool, str]:
            unit = units[unit_name]
            healthy = (
                unit.workload_status.current == "active"
                and unit.juju_status.current == "idle"
            )
            return not healthy, unit_name in target_units, unit_name

        self._units = sorted(surviving_units + target_units, key=unit_sort_key)
        self.unit = self._units[0]

        return Result(ResultType.COMPLETED)

    def is_skip(self, context: StepContext) -> Result:
        """Determine whether cleanup can run and whether it is needed."""
        return self._prepare()

    def _run_command(self, command: str, *, allow_fallback: bool) -> str:
        """Run a MicroCeph command and fail on transport or command errors."""
        if self.unit is None:
            raise SunbeamException("MicroCeph cleanup unit is not available")

        candidates = [self.unit]
        if allow_fallback:
            candidates.extend(unit for unit in self._units if unit != self.unit)
        for index, unit in enumerate(candidates):
            try:
                result = self.jhelper.run_cmd_on_machine_unit_payload(
                    unit,
                    self.model,
                    command,
                    timeout=self._COMMAND_TIMEOUT,
                )
            except ExecFailedException as e:
                if index < len(candidates) - 1:
                    LOG.warning(
                        "Failed to run MicroCeph command on %s: %r; "
                        "trying another unit",
                        unit,
                        e,
                    )
                    continue
                raise SunbeamException(f"Failed to run {command!r}: {e}") from e
            self.unit = unit
            break

        return result.stdout

    @staticmethod
    def _parse_configured_disks(stdout: Any) -> list[dict[str, Any]]:
        """Parse MicroCeph's configured disk records."""
        if not isinstance(stdout, str):
            raise ValueError("Configured disk listing output is not text")
        payload = json.loads(stdout)
        if not isinstance(payload, dict) or "ConfiguredDisks" not in payload:
            raise ValueError("Configured disk listing has no ConfiguredDisks")
        configured_disks = payload["ConfiguredDisks"]
        if not isinstance(configured_disks, list):
            raise ValueError("ConfiguredDisks is not a list")

        required_fields = {"osd", "location"}
        disks: list[dict[str, Any]] = []
        for disk in configured_disks:
            if not isinstance(disk, dict) or not required_fields <= set(disk):
                raise ValueError("Configured disk has an invalid schema")
            if type(disk["osd"]) is not int or disk["osd"] < 0:
                raise ValueError("Configured disk has an invalid OSD ID")
            if not isinstance(disk["location"], str):
                raise ValueError("Configured disk has an invalid location")
            disks.append(disk)
        return disks

    @staticmethod
    def _parse_crush_tree(stdout: Any) -> list[dict[str, Any]]:
        """Parse the JSON nodes returned by the Ceph OSD tree command."""
        if not isinstance(stdout, str):
            raise ValueError("CRUSH tree output is not text")
        tree = json.loads(stdout)
        if not isinstance(tree, dict) or not isinstance(tree.get("nodes"), list):
            raise ValueError("CRUSH tree output has no nodes list")

        nodes = tree["nodes"]
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("CRUSH tree node is not an object")
            if not isinstance(node.get("name"), str) or not isinstance(
                node.get("type"), str
            ):
                raise ValueError("CRUSH tree node has an invalid schema")
            if node.get("type") == "host" and not isinstance(
                node.get("children"), list
            ):
                raise ValueError("CRUSH host has no children list")
            if "children" in node and not isinstance(node["children"], list):
                raise ValueError("CRUSH tree node children is not a list")
        return nodes

    def _list_configured_osd_ids(self) -> list[int]:
        """Return target OSD IDs that still exist in the MicroCeph database."""
        try:
            disks = self._parse_configured_disks(
                self._run_command("microceph disk list --json", allow_fallback=True)
            )
        except ValueError as e:
            raise SunbeamException(
                f"Failed to parse configured disk listing: {e}"
            ) from e
        return sorted({disk["osd"] for disk in disks if disk["location"] == self.node})

    def _list_crush_osd_ids(self) -> list[int]:
        """Return OSD IDs under the target CRUSH host."""
        try:
            nodes = self._parse_crush_tree(
                self._run_command(
                    "microceph.ceph osd tree --format json", allow_fallback=True
                )
            )
        except ValueError as e:
            raise SunbeamException(f"Failed to parse CRUSH tree: {e}") from e

        host = next(
            (
                node
                for node in nodes
                if node["name"] == self.node and node["type"] == "host"
            ),
            None,
        )
        if host is None:
            return []
        children = host["children"]
        if any(type(child) is not int or child < 0 for child in children):
            raise SunbeamException("CRUSH host has an invalid OSD ID")
        return sorted(set(children))

    def _list_target_osds(self) -> tuple[list[int], list[int]]:
        """Read both target OSD sources before changing either source."""
        return self._list_configured_osd_ids(), self._list_crush_osd_ids()

    def run(self, context: StepContext) -> Result:
        """Remove DB-backed OSDs and verify both MicroCeph and CRUSH state."""
        if self.unit is None:
            preparation = self._prepare()
            if preparation.result_type != ResultType.COMPLETED:
                return preparation

        try:
            configured_osds, crush_osds = self._list_target_osds()
            crush_only_osds = sorted(set(crush_osds) - set(configured_osds))
            if crush_only_osds:
                return Result(
                    ResultType.FAILED,
                    f"CRUSH-only OSDs for {self.node}: {crush_only_osds}",
                )

            for osd_id in configured_osds:
                command = (
                    f"microceph disk remove osd.{osd_id} "
                    f"--timeout {self._OSD_REMOVE_TIMEOUT}"
                )
                if self.force:
                    command += (
                        " --confirm-failure-domain-downgrade --bypass-safety-checks"
                    )
                self._run_command(command, allow_fallback=False)

            if not configured_osds:
                return Result(ResultType.COMPLETED)

            remaining_configured, remaining_crush = self._list_target_osds()
            if remaining_configured:
                return Result(
                    ResultType.FAILED,
                    f"Configured OSDs remain for {self.node}: {remaining_configured}",
                )
            if remaining_crush:
                return Result(
                    ResultType.FAILED,
                    f"CRUSH OSDs remain for {self.node}: {remaining_crush}",
                )
        except SunbeamException as e:
            LOG.debug("Failed to clean up MicroCeph OSDs", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


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
        self.wipe = False

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
            LOG.debug("Unpartitioned disks: %s", self.unpartitioned_disks)
            LOG.debug("OSD disks: %s", self.osd_disks)

        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug("Failed to list disks: %r", e)
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
        wipe_disks = microceph_config.get(self.node_name, {}).get(
            "dangerous_i_acknowledge_i_will_lose_data_wipe_disks", False
        )
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
        self.wipe = wipe_disks
        self.variables["microceph_config"][self.node_name]["osd_devices"] = self.disks
        # note(gboutry): wipe disks option is never saved in clusterd, always
        # read when needed in the manifest.

        LOG.debug("Microceph variables: %s", self.variables)
        questions.write_answers(self.client, self._CONFIG, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def is_skip(self, context: StepContext) -> Result:
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

    def run(self, context: StepContext) -> Result:
        """Configure local disks on microceph."""
        failed = False
        action_params: dict[str, Any] = {"device-id": self.disks}
        if self.wipe:
            LOG.debug("User expressly accepted to wipe disks before adding OSDs")
            action_params["wipe"] = True
        try:
            unit = self.jhelper.get_unit_from_machine(
                APPLICATION, self.machine_id, self.model
            )
            LOG.debug("Running action add-osd on %s", unit)
            action_result = self.jhelper.run_action(
                unit,
                self.model,
                "add-osd",
                action_params=action_params,
            )
            LOG.debug("Result after running action add-osd: %s", action_result)
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
                            LOG.debug("Disk %s is already added", disk)
                            continue
                        else:
                            failed = True
            except Exception as e:
                LOG.debug("Exception in eval action output: %r", e)
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

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        if len(self.storage_nodes):
            return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, context: StepContext) -> Result:
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
                "Running microceph action set-pool-size with params %s", action_params
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
            LOG.debug("Failed to update pool size for %s", pools, exc_info=True)
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

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            node_info = self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(
                ResultType.SKIPPED, f"Node {self.node} is not found in the cluster"
            )

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

    def run(self, context: StepContext) -> Result:
        """Destroy microceph application."""
        # note(gboutry):this is a workaround for
        # https://github.com/juju/terraform-provider-juju/issues/473
        try:
            resources = self.tfhelper.state_list()
        except TerraformException as e:
            LOG.debug("Failed to list Terraform state: %r", e)
            return Result(ResultType.FAILED, "Failed to list terraform state")

        for resource in resources:
            if "integration" in resource:
                try:
                    self.tfhelper.state_rm(resource)
                except TerraformException as e:
                    LOG.debug("Failed to remove resource %s: %r", resource, e)
                    return Result(
                        ResultType.FAILED,
                        f"Failed to remove resource {resource} from state",
                    )

        return super().run(context)
