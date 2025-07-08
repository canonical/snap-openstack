# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import contextlib
import json
import logging
from functools import cache
from typing import Any, Tuple

from rich.console import Console
from rich.status import Status

import sunbeam.core.questions
from sunbeam import devspec, utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    PCI_CONFIG_SECTION,
    SetHypervisorUnitsOptionsStep,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    SunbeamException,
)
from sunbeam.core.juju import ActionFailedException, JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.questions import (
    ConfirmQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.steps import hypervisor
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.clusterd import CLUSTERD_PORT

LOG = logging.getLogger(__name__)
console = Console()


def local_hypervisor_questions():
    return {
        "nics": sunbeam.core.questions.PromptQuestion(
            "External network's interface",
            description=(
                "Interface used by networking layer to allow remote access to cloud"
                " instances. This interface must be unconfigured"
                " (no IP address assigned) and connected to the external network."
            ),
        ),
    }


def _fetch_nics(client: Client, name: str, jhelper: JujuHelper, model: str):
    # TODO: consider caching this.
    LOG.debug("Fetching nics...")
    node = client.cluster.get_node_info(name)
    machine_id = str(node.get("machineid"))
    unit = jhelper.get_unit_from_machine("openstack-hypervisor", machine_id, model)
    action_result = jhelper.run_action(unit, model, "list-nics")
    return json.loads(action_result.get("result", "{}"))


class LocalSetHypervisorUnitsOptionsStep(SetHypervisorUnitsOptionsStep):
    def __init__(
        self,
        client: Client,
        name: str,
        jhelper: JujuHelper,
        model: str,
        join_mode: bool = False,
        manifest: Manifest | None = None,
    ):
        super().__init__(
            client,
            [name],
            jhelper,
            model,
            manifest,
            "Apply local hypervisor settings",
            "Applying local hypervisor settings",
        )
        self.join_mode = join_mode

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def prompt_for_nic(self, console: Console | None = None) -> str | None:
        """Prompt user for nic to use and do some validation."""
        if console:
            context: Any = console.status("Fetching candidate nics from hypervisor")
        else:
            context = contextlib.nullcontext()

        with context:
            nics = _fetch_nics(self.client, self.names[0], self.jhelper, self.model)

        all_nics: list[dict] | None = nics.get("nics")
        candidate_nics: list[str] | None = nics.get("candidates")

        if not all_nics:
            # all_nics should contain every nics of the hypervisor
            # how did we get a response if there's no nics?
            raise SunbeamException("No nics found on hyperisor")

        if not candidate_nics:
            raise SunbeamException("No candidate nics found")

        local_hypervisor_bank = sunbeam.core.questions.QuestionBank(
            questions=local_hypervisor_questions(),
            console=console,
            accept_defaults=False,
        )
        nic = None
        while True:
            nic = local_hypervisor_bank.nics.ask(
                new_default=candidate_nics[0], new_choices=candidate_nics
            )
            if not nic:
                continue
            nic_state = None
            for interface in all_nics:
                if interface["name"] == nic:
                    nic_state = interface
                    break
            if not nic_state:
                continue
            LOG.debug("Selected nic %s, state: %r", nic, nic_state)
            if nic_state["configured"]:
                agree_nic_up = sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is configured. Any "
                    "configuration will be lost, are you sure you want to "
                    "continue?",
                ).ask()
                if not agree_nic_up:
                    continue
            if nic_state["up"] and not nic_state["connected"]:
                agree_nic_no_link = sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is not connected. Are "
                    "you sure you want to continue?",
                    description=(
                        "Interface is not detected as connected to any network. This"
                        " means it will most likely not work as expected."
                    ),
                ).ask()
                if not agree_nic_no_link:
                    continue
            break
        return nic

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Determines if the step can take input from the user."""
        # If adding a node before configure step has run then answers will
        # not be populated yet.
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        remote_access_location = self.variables.get("user", {}).get(
            "remote_access_location"
        )
        # If adding new nodes to the cluster then local access makes no sense
        # so always prompt for the nic.
        preseed = {}
        if self.manifest and (
            ext_network := self.manifest.core.config.external_network
        ):
            preseed = ext_network.model_dump(by_alias=True)

        if self.join_mode or remote_access_location == utils.REMOTE_ACCESS:
            # If nic is in the preseed assume the user knows what they are doing and
            # bypass validation
            host = self.names[0]
            nics = preseed.get("nics")
            if nics and (nic := nics.get(host)):
                self.nics[host] = nic
                return

            if nic := preseed.get("nic"):
                LOG.warning(
                    "DEPRECATED: Using deprecated `nic` field for host %r", host
                )
                self.nics[host] = nic
                return
            self.nics[host] = self.prompt_for_nic(console)


class LocalClusterStatusStep(ClusterStatusStep):
    def models(self) -> list[str]:
        """List of models to query status from."""
        return [self.deployment.openstack_machines_model]

    @cache
    def _has_storage(self) -> bool:
        """Check if deployment has storage."""
        return (
            len(self.deployment.get_client().cluster.list_nodes_by_role("storage")) > 0
        )

    def map_application_status(self, application: str, status: str) -> str:
        """Callback to map application status to a column.

        This callback is called for every unit status with the name of its application.
        """
        if application == hypervisor.APPLICATION:
            if status == "waiting" and not self._has_storage():
                return "active"
        return status

    def _get_microcluster_status(self) -> dict:
        """Get microcluster status.

        Override this method to include microcluster member address as well in
        the status.
        This is required due to workaround bug
        https://github.com/juju/juju/issues/18641
        """
        client = self.deployment.get_client()
        try:
            cluster_status = client.cluster.get_status()
        except ClusterServiceUnavailableException:
            LOG.debug("Failed to query cluster status", exc_info=True)
            raise SunbeamException("Cluster service is not yet bootstrapped.")
        status = {}
        for node, _status in cluster_status.items():
            status[node] = {
                "address": _status.get("address"),
                "status": _status.get("status"),
            }
        return status

    def _update_microcluster_status(self, status: dict, microcluster_status: dict):
        """Update microcluster status in the status dict.

        If the hostname in status and microcluster_status does not match, compare
        with ip address in microcluster_status and update hostname and cluster
        status accordingly.
        """
        members = microcluster_status.keys()
        for node_status in status[self.deployment.openstack_machines_model].values():
            node_name = node_status.get("name")
            if node_name not in members:
                for member, member_status in microcluster_status.items():
                    # If node name does not match in microcluster status and status,
                    # check if it matches with ip address in microcluster status. This
                    # situation can happen due to
                    # https://github.com/juju/juju/issues/18641
                    # Replace node name with actual hostname from microcluster status.
                    if (
                        member_status.get("address").removesuffix(f":{CLUSTERD_PORT}")
                        == node_name
                    ):
                        LOG.debug(
                            f"Node name matched with address {node_name}, change name "
                            f"to {member}"
                        )
                        node_name = member
                        node_status["name"] = member

            node_status["clusterd-status"] = microcluster_status.get(node_name, {}).get(
                "status"
            )


def sriov_questions():
    return {
        "configure_sriov": ConfirmQuestion(
            "Configure SR-IOV?",
            default_value=False,
            description=(
                "This allows specifying a list of SR-IOV devices that "
                "will be exposed to Openstack instances."
            ),
        ),
    }


class LocalConfigSRIOVStep(BaseStep):
    """Prompt user for SR-IOV configuration."""

    # TODO: this might be reused as a maas step.

    def __init__(
        self,
        client: Client,
        node_name: str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("SR-IOV Settings", "Ask user for SR-IOV settings")
        self.client = client
        self.node_name = node_name
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        if not console:
            LOG.info("No console available, skipping prompt.")
            return

        self.variables = load_answers(self.client, PCI_CONFIG_SECTION)

        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {}

        if self.manifest:
            if (
                self.manifest.core.config.pci
                and self.manifest.core.config.pci.device_specs
            ):
                pci_whitelist = self.manifest.core.config.pci.device_specs
                LOG.debug("PCI whitelist from manifest: %s", pci_whitelist)
            if (
                self.manifest.core.config.pci
                and self.manifest.core.config.pci.excluded_devices
            ):
                excluded_devices = self.manifest.core.config.pci.excluded_devices
                LOG.debug("PCI exclude list from manifest: %s", excluded_devices)

        previous_pci_whitelist = self.variables.get("pci_whitelist") or []
        previous_excluded_devices = self.variables.get("excluded_devices") or {}
        previous_node_excluded_devices = (
            previous_excluded_devices.get(self.node_name) or []
        )
        LOG.debug("PCI whitelist from previous answers: %s", previous_pci_whitelist)
        LOG.debug(
            "PCI exclude list from previous answers: %s", previous_excluded_devices
        )

        if self.node_name not in excluded_devices:
            excluded_devices[self.node_name] = []

        for device_spec in previous_pci_whitelist:
            if device_spec not in pci_whitelist:
                pci_whitelist.append(device_spec)
        for excluded_device in previous_node_excluded_devices:
            if excluded_device not in excluded_devices[self.node_name]:
                excluded_devices[self.node_name].append(excluded_device)

        sriov_bank = QuestionBank(
            questions=sriov_questions(),
            console=console,
            preseed=None,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        nics = _fetch_nics(self.client, self.node_name, self.jhelper, self.model)
        sriov_nics = [nic for nic in nics["nics"] if nic.get("sriov_available")]

        if sriov_nics:
            configure_sriov = sriov_bank.configure_sriov.ask()
            if configure_sriov:
                self._show_sriov_nics(
                    console, sriov_nics, pci_whitelist, excluded_devices
                )

                for nic in sriov_nics:
                    nic_str_repr = self._get_nic_str_repr(nic)
                    question = (
                        f"Specify the physical network for {nic_str_repr} "
                        "or specify none to exclude the device."
                    )
                    physnet = sunbeam.core.questions.PromptQuestion(
                        question,
                        default_value="physnet1",
                    ).ask()
                    if not physnet or physnet.lower() == "none":
                        self._exclude_sriov_nic(nic, pci_whitelist, excluded_devices)
                    else:
                        self._whitelist_sriov_nic(
                            nic, pci_whitelist, excluded_devices, physnet
                        )

        else:
            LOG.info("No SR-IOV devics detected, skipping SR-IOV configuration.")

        LOG.info("Updated PCI device whitelist: %s", pci_whitelist)
        LOG.info("Updated PCI device exclusion list: %s", excluded_devices)

        self.variables["pci_whitelist"] = pci_whitelist
        self.variables["excluded_devices"] = excluded_devices

        write_answers(self.client, PCI_CONFIG_SECTION, self.variables)

    def _is_sriov_nic_whitelisted(
        self, nic: dict, pci_whitelist: list[dict], excluded_devices: dict[str, list]
    ) -> Tuple[bool, str | None]:
        """Returns the (is_whitelisted>, physnet) tuple."""
        pci_address = nic["pci_address"]

        node_excluded_devices = excluded_devices.get(self.node_name) or []
        if pci_address in node_excluded_devices:
            return False, None

        for spec_dict in pci_whitelist:
            if not isinstance(spec_dict, dict):
                raise ValueError(
                    "Invalid device spec, expecting a dict: %s." % spec_dict
                )

            pci_spec = devspec.PciDeviceSpec(spec_dict)
            dev = {
                "vendor_id": nic["vendor_id"].lstrip("0x"),
                "product_id": nic["product_id"].lstrip("0x"),
                "address": nic["pci_address"],
                "parent_addr": nic["pf_pci_address"],
            }
            match = pci_spec.match(dev)
            if match:
                return True, spec_dict.get("physical_network")

        return False, None

    def _whitelist_sriov_nic(
        self,
        nic: dict,
        pci_whitelist: list[dict],
        excluded_devices: dict[str, list],
        physnet: str,
    ):
        LOG.debug("Whitelisting SR-IOV nic: %s %s", nic["name"], nic["pci_address"])
        pci_address = nic["pci_address"]

        node_excluded_devices = excluded_devices.get(self.node_name) or []
        if pci_address in node_excluded_devices:
            LOG.debug(
                "Removing SR-IOV nic from the exclusion list: %s %s",
                nic["name"],
                nic["pci_address"],
            )
            node_excluded_devices.remove(pci_address)

        # Update the global whitelist if needed.
        whitelisted = self._is_sriov_nic_whitelisted(
            nic, pci_whitelist, excluded_devices
        )[0]
        if not whitelisted:
            new_dev_spec = {
                "address": nic["pci_address"],
                "vendor_id": nic["vendor_id"].lstrip("0x"),
                "product_id": nic["product_id"].lstrip("0x"),
                "physical_network": physnet,
            }
            pci_whitelist.append(new_dev_spec)
        else:
            LOG.debug(
                "SR-IOV nic already whitelisted: %s %s", nic["name"], nic["pci_address"]
            )

    def _exclude_sriov_nic(
        self, nic: dict, pci_whitelist: list[dict], excluded_devices: dict[str, list]
    ):
        LOG.debug("Excluding SR-IOV nic: %s", nic["name"])
        if self.node_name not in excluded_devices:
            excluded_devices[self.node_name] = []
        if nic["pci_address"] not in excluded_devices[self.node_name]:
            excluded_devices[self.node_name].append(nic["pci_address"])

    def _get_nic_str_repr(self, nic: dict):
        """Get the nic string representation."""
        vendor = nic.get("vendor_name") or nic.get("vendor_id") or "Unknown vendor"
        product = nic.get("product_name") or nic.get("product_id") or "Unknown product"
        name = nic.get("name") or "Unknown ifname"
        return f"{vendor} {product} ({name})"

    def _show_sriov_nics(
        self,
        console: Console,
        sriov_nics: list[dict],
        pci_whitelist: list[dict],
        excluded_devices: dict[str, list],
    ):
        if not sriov_nics:
            return

        console.print("Found the following SR-IOV capable devices:")

        for nic in sriov_nics:
            whitelisted, physnet = self._is_sriov_nic_whitelisted(
                nic, pci_whitelist, excluded_devices
            )
            checkbox = "X" if whitelisted else " "
            nic_str_repr = self._get_nic_str_repr(nic)

            nic_info = f"  \\[{checkbox}] {nic_str_repr} \\[physnet: {physnet}]"
            console.print(nic_info)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def run(self, status: Status | None = None) -> Result:
        """Apply individual hypervisor settings."""
        app = "openstack-hypervisor"
        action_cmd = "set-hypervisor-local-settings"
        name = self.node_name

        self.update_status(status, f"setting PCI configuration for {name}")

        excluded_devices = self.variables.get("excluded_devices") or {}
        node_excluded_devices = excluded_devices.get(name) or []
        LOG.debug("PCI excluded devices [%s]: %s", name, node_excluded_devices)

        node = self.client.cluster.get_node_info(name)
        self.machine_id = str(node.get("machineid"))
        unit = self.jhelper.get_unit_from_machine(app, self.machine_id, self.model)
        try:
            self.jhelper.run_action(
                unit,
                self.model,
                action_cmd,
                action_params={
                    "pci-excluded-devices": json.dumps(node_excluded_devices),
                },
            )
        except (ActionFailedException, TimeoutError):
            msg = f"Unable to set hypervisor {name} configuration"
            LOG.debug(msg, exc_info=True)
            return Result(ResultType.FAILED, msg)

        return Result(ResultType.COMPLETED)
