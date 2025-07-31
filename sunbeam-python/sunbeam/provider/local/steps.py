# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import contextlib
import json
import logging
from functools import cache
from typing import Any

from rich.console import Console
from rich.status import Status

import sunbeam.core.questions
from sunbeam import utils
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
from sunbeam.provider.common import nic_utils
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
            nics = nic_utils.fetch_nics(
                self.client, self.names[0], self.jhelper, self.model
            )

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
        "configure_sriov": sunbeam.core.questions.ConfirmQuestion(
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

    def __init__(
        self,
        client: Client,
        node_name: str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
        show_initial_prompt: bool = True,
    ):
        super().__init__("SR-IOV Settings", "Configure SR-IOV")
        self.client = client
        self.node_name = node_name
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}
        # Avoid the "Configure SR-IOV?" question if the user
        # specifically asked for this.
        self.show_initial_prompt = show_initial_prompt

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

        self.variables = sunbeam.core.questions.load_answers(
            self.client, PCI_CONFIG_SECTION
        )

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

        LOG.debug("PCI whitelist from previous answers: %s", previous_pci_whitelist)
        LOG.debug(
            "PCI exclude list from previous answers: %s", previous_excluded_devices
        )

        if self.node_name not in excluded_devices:
            excluded_devices[self.node_name] = []

        if self.accept_defaults:
            LOG.debug(
                "Received '--accept-defaults', ignoring previous "
                "answers and applying the manifest configuration."
            )
        else:
            for device_spec in previous_pci_whitelist:
                if device_spec not in pci_whitelist:
                    pci_whitelist.append(device_spec)
            for node in previous_excluded_devices:
                if node not in excluded_devices:
                    excluded_devices[node] = previous_excluded_devices[node]
                else:
                    for excluded_device in previous_excluded_devices[node]:
                        if excluded_device not in excluded_devices[node]:
                            excluded_devices[node].append(excluded_device)

            self._do_prompt(pci_whitelist, excluded_devices, show_hint)

        LOG.info("Updated PCI device whitelist: %s", pci_whitelist)
        LOG.info("Updated PCI device exclusion list: %s", excluded_devices)

        self.variables["pci_whitelist"] = pci_whitelist
        self.variables["excluded_devices"] = excluded_devices

        sunbeam.core.questions.write_answers(
            self.client, PCI_CONFIG_SECTION, self.variables
        )

    def _do_prompt(
        self,
        pci_whitelist: list[dict],
        excluded_devices: dict[str, list],
        show_hint: bool = False,
    ):
        sriov_bank = sunbeam.core.questions.QuestionBank(
            questions=sriov_questions(),
            console=console,
            preseed=None,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        nics = nic_utils.fetch_nics(
            self.client, self.node_name, self.jhelper, self.model
        )

        pci_address_map: dict[str, str] = {}
        sriov_nics = []
        for nic in nics["nics"]:
            nic_name = nic["name"]
            pci_address = nic["pci_address"]

            if not nic["sriov_available"]:
                LOG.debug("The nic does not support SR-IOV: %s", nic_name)
                continue
            if not pci_address:
                LOG.debug("No nic PCI address: %s", nic_name)
                continue
            if pci_address in pci_address_map:
                # We'll filter out interfaces that have duplicate PCI addresses,
                # keeping only the first occurrence.
                #
                # For example, Mellanox ConnectX 6 will create one representor
                # network function for each VF, having the same address as the PF.
                #
                # Bus info          Device          Class      Description
                # ========================================================
                # pci@0000:03:00.0  enp3s0f0np0     network    ConnectX-6 Dx
                # pci@0000:03:00.1  enp3s0f1np1     network    ConnectX-6 Dx
                # pci@0000:03:00.2  enp3s0f0v0      network    ConnectX Family mlx5 VF
                # pci@0000:03:00.3  enp3s0f0v1      network    ConnectX Family mlx5 VF
                # ...
                # pci@0000:03:00.0  enp3s0f0r0      network    Ethernet interface
                # pci@0000:03:00.0  enp3s0f0r1      network    Ethernet interface
                LOG.debug(
                    "Duplicate PCI address: %s, interface names: %s %s.",
                    pci_address,
                    nic_name,
                    pci_address_map[pci_address],
                )
                continue

            pci_address_map[pci_address] = nic_name
            sriov_nics.append(nic)

        if sriov_nics:
            if self.show_initial_prompt:
                configure_sriov = sriov_bank.configure_sriov.ask()
            else:
                configure_sriov = True

            if configure_sriov:
                self._show_sriov_nics(
                    console, sriov_nics, pci_whitelist, excluded_devices
                )

                for nic in sriov_nics:
                    nic_str_repr = self._get_nic_str_repr(nic)
                    whitelisted, physnet = nic_utils.is_sriov_nic_whitelisted(
                        self.node_name, nic, pci_whitelist, excluded_devices
                    )

                    question = f"Add network adapter to PCI whitelist? {nic_str_repr} "
                    should_whitelist = sunbeam.core.questions.ConfirmQuestion(
                        question, default_value=whitelisted
                    ).ask()
                    if not should_whitelist:
                        nic_utils.exclude_sriov_nic(
                            self.node_name, nic, pci_whitelist, excluded_devices
                        )
                        continue

                    question = (
                        f"Specify the physical network for {nic_str_repr} "
                        "or pass 'no-physnet' if using hardware offloading with "
                        "overlay networks"
                    )
                    physnet = sunbeam.core.questions.PromptQuestion(
                        question,
                        default_value=physnet,
                    ).ask()
                    nic_utils.whitelist_sriov_nic(
                        self.node_name, nic, pci_whitelist, excluded_devices, physnet
                    )

        else:
            LOG.info("No SR-IOV devics detected, skipping SR-IOV configuration.")

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
            whitelisted, physnet = nic_utils.is_sriov_nic_whitelisted(
                self.node_name, nic, pci_whitelist, excluded_devices
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
            LOG.error(msg, exc_info=True)
            return Result(ResultType.FAILED, msg)

        return Result(ResultType.COMPLETED)
