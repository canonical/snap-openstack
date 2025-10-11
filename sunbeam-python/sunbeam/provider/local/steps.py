# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import contextlib
import ipaddress
import json
import logging
from functools import cache
from typing import Any

import click
from rich.console import Console
from rich.status import Status

import sunbeam.core.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    PCI_CONFIG_SECTION,
    BaseConfigDPDKStep,
    SetHypervisorUnitsOptionsStep,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    SunbeamException,
    parse_ip_range_or_cidr,
)
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuStepHelper,
    UnitNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.provider.common import nic_utils
from sunbeam.steps import hypervisor
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.clusterd import CLUSTERD_PORT
from sunbeam.steps.k8s import get_loadbalancer_config
from sunbeam.steps.openstack import EndpointsConfigurationStep

LOG = logging.getLogger(__name__)
console = Console()
MICROOVN_APPLICATION = "microovn"


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
        clear_previous_config: bool = False,
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
        self.should_skip = False
        self.clear_previous_config = clear_previous_config

    def prompt(  # noqa: C901
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
            pci_config = self.manifest.core.config.pci
            if pci_config and pci_config.device_specs:
                pci_whitelist = pci_config.device_specs
                LOG.debug("PCI whitelist from manifest: %s", pci_whitelist)
            if pci_config and pci_config.excluded_devices:
                excluded_devices = pci_config.excluded_devices
                LOG.debug("PCI exclude list from manifest: %s", excluded_devices)

        previous_pci_whitelist = self.variables.get("pci_whitelist") or []
        previous_excluded_devices = self.variables.get("excluded_devices") or {}

        LOG.debug("PCI whitelist from previous answers: %s", previous_pci_whitelist)
        LOG.debug(
            "PCI exclude list from previous answers: %s", previous_excluded_devices
        )

        if self.node_name not in excluded_devices:
            excluded_devices[self.node_name] = []

        if not self.clear_previous_config:
            logging.debug("Picking up previous answers.")
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
        else:
            # The user requested to drop the previous answers instead of merging the
            # device lists with the previous ones.
            logging.debug("Dropping previous answers.")

        if not self.accept_defaults:
            self._do_prompt(pci_whitelist, excluded_devices, show_hint)

        LOG.info("Updated PCI device whitelist: %s", pci_whitelist)
        LOG.info("Updated PCI device exclusion list: %s", excluded_devices)

        # Handle PCI passthrough devices
        # All GPU devices returned by openstack-hypervisor will be added
        # as PCI passthrough devices to pci_whitelist.
        try:
            snap_gpus = nic_utils.fetch_gpus(
                self.client, self.node_name, self.jhelper, self.model
            )
        except (UnitNotFoundException, ActionFailedException) as e:
            LOG.debug(
                f"Failed fetching GPUs from node {self.node_name}",
                exc_info=True,
            )
            raise click.ClickException(
                f"Failed in fetching GPUs from node {self.node_name}"
            ) from e

        for snap_gpu in snap_gpus["gpus"]:
            nic_utils.whitelist_pci_passthrough_device(
                self.node_name, snap_gpu, pci_whitelist, excluded_devices
            )

        LOG.info(
            "PCI device whitelist information after handling PCI passthrough devices:"
        )
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
                    nic_str_repr = nic_utils.get_nic_str_repr(nic)
                    whitelisted, physnet = nic_utils.is_sriov_nic_whitelisted(
                        self.node_name, nic, pci_whitelist, excluded_devices
                    )

                    question = f"Add network adapter to PCI whitelist? {nic_str_repr} "
                    should_whitelist = sunbeam.core.questions.ConfirmQuestion(
                        question, default_value=whitelisted
                    ).ask()
                    if not should_whitelist:
                        nic_utils.exclude_sriov_nic(
                            self.node_name, nic, excluded_devices
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
            LOG.info("No SR-IOV devices detected, skipping SR-IOV configuration.")
            self.should_skip = True

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
            nic_str_repr = nic_utils.get_nic_str_repr(nic)

            nic_info = f"  \\[{checkbox}] {nic_str_repr} \\[physnet: {physnet}]"
            console.print(nic_info)

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
        if self.should_skip:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

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


class LocalEndpointsConfigurationStep(EndpointsConfigurationStep):
    """Configuration endpoints for local provider."""

    def __init__(
        self,
        client: Client,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(client, manifest, accept_defaults)
        self.loadbalancer_range = None

    def _loadbalancer_range(self):
        """Load the load balancer range."""
        if not self.loadbalancer_range:
            loadbalancer_range = get_loadbalancer_config(self.client)
            if loadbalancer_range is None:
                raise SunbeamException(
                    "Load balancer range is not configured. Please configure it first."
                )
            self.loadbalancer_range = parse_ip_range_or_cidr(loadbalancer_range)
        return self.loadbalancer_range

    def _validate_endpoint(self, endpoint: str, ip: str) -> bool:
        """Let's validate the endpoint.

        # TODO(gboutry): Endpoint is ignored in Local Mode because we
        # cannot yet configure loadbalancers per network space yet.
        """
        ip_address = ipaddress.ip_address(ip)
        loadbalancer_range = self._loadbalancer_range()

        if isinstance(
            loadbalancer_range, (ipaddress.IPv4Network, ipaddress.IPv6Network)
        ):
            if ip_address.version != loadbalancer_range.version:
                LOG.debug(
                    "IP version mismatch: ip=%s (v%d) vs loadbalancer_range=%s (v%d)",
                    ip_address,
                    ip_address.version,
                    loadbalancer_range,
                    loadbalancer_range.version,
                )
                return False
            is_in_range = ip_address in loadbalancer_range
            LOG.debug(
                "IP %s %s in loadbalancer network %s",
                ip_address,
                "is" if is_in_range else "is not",
                loadbalancer_range,
            )
            return is_in_range
        elif isinstance(loadbalancer_range, tuple):
            start_ip, end_ip = loadbalancer_range
            if (
                ip_address.version != start_ip.version
                or start_ip.version != end_ip.version
            ):
                LOG.debug(
                    "IP version mismatch in range: ip=%s (v%d) vs range=%s-%s "
                    "(v%d-v%d)",
                    ip_address,
                    ip_address.version,
                    start_ip,
                    end_ip,
                    start_ip.version,
                    end_ip.version,
                )
                return False
            is_in_range = start_ip <= ip_address <= end_ip  # type: ignore
            LOG.debug(
                "IP %s %s in loadbalancer range %s-%s",
                ip_address,
                "is" if is_in_range else "is not",
                start_ip,
                end_ip,
            )
            return is_in_range
        else:
            LOG.debug(
                "Invalid loadbalancer_range type: %s (expected IPv4Network, "
                "IPv6Network, or tuple)",
                type(loadbalancer_range).__name__,
            )
            return False


class LocalConfigDPDKStep(BaseConfigDPDKStep):
    """Prompt the user for DPDK configuration.

    Local deployment steps.
    """

    def __init__(
        self,
        client: Client,
        node_name: str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(client, jhelper, model, manifest, accept_defaults)
        self.node_name = node_name

    def _prompt_nics(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        if not console:
            return

        if not self.variables.get("nics"):
            self.variables["nics"] = {}
        previous_nics = self.variables["nics"].get(self.node_name) or []

        dpdk_manifest_ports = self._get_dpdk_manifest_ports() or {}
        if dpdk_manifest_ports.get(self.node_name):
            self.nics = dpdk_manifest_ports[self.node_name]
            logging.debug("DPDK ports specified through the manifest: %s", self.nics)
            return

        with console:
            nics = nic_utils.fetch_nics(
                self.client, self.node_name, self.jhelper, self.model
            )

        all_nics: list[dict] = nics.get("nics") or []
        candidate_nics: list[dict] = []
        enabled_nic_names: list[str] = []

        LOG.debug("Determining DPDK candidate interfaces.")
        for nic in all_nics:
            if not nic.get("name"):
                # Note that the interface name will no longer be visible once
                # assigned to the "vfio-pci" driver.
                LOG.debug("No interface name: %s, skipping.", nic.get("pci_address"))
                continue
            if nic.get("pf_pci_address"):
                LOG.debug("Ignoring SR-IOV VF: %s.", nic.get("name"))
                continue
            if not nic.get("pci_address"):
                LOG.debug("Not a PCI device: %s.", nic.get("name"))
                continue
            if nic.get("configured"):
                LOG.debug("The interface has an IP assigned, skipping.")
                continue

            candidate_nics.append(nic)

        if not candidate_nics:
            LOG.info("No candidate DPDK interfaces.")
            return

        console.print("Configuring DPDK physical interfaces.")
        console.print(
            "\nWARNING: the specified interfaces will be reconfigured to use a "
            "DPDK-compatible driver (vfio-pci by default) and will no longer "
            "be visible to the host."
        )
        console.print(
            "Any bonds and bridges defined in MAAS/Netplan will be "
            "updated to use the new DPDK OVS ports."
        )
        console.print("\nDPDK candidate interfaces:")
        for nic in candidate_nics:
            nic_str_repr = nic_utils.get_nic_str_repr(nic)
            console.print(f"* {nic_str_repr}")

        for nic in candidate_nics:
            nic_str_repr = nic_utils.get_nic_str_repr(nic)
            question = f"Enable interface DPDK mode? {nic_str_repr}"
            enable_dpdk = sunbeam.core.questions.ConfirmQuestion(
                question,
                default_value=(nic["name"] in previous_nics),
                accept_defaults=self.accept_defaults,
            ).ask()
            if enable_dpdk:
                enabled_nic_names.append(nic["name"])

        self.nics = enabled_nic_names
        self.variables["nics"][self.node_name] = enabled_nic_names

    def run(self, status: Status | None = None) -> Result:
        """Apply individual hypervisor settings."""
        app = "openstack-hypervisor"
        action_cmd = "set-hypervisor-local-settings"
        name = self.node_name

        self.update_status(status, f"setting DPDK ports for {name}: {self.nics}")

        node = self.client.cluster.get_node_info(name)
        self.machine_id = str(node.get("machineid"))
        unit = self.jhelper.get_unit_from_machine(app, self.machine_id, self.model)
        try:
            self.jhelper.run_action(
                unit,
                self.model,
                action_cmd,
                action_params={
                    "ovs-dpdk-ports": ",".join(self.nics or ""),
                },
            )
        except (ActionFailedException, TimeoutError):
            msg = f"Unable to set hypervisor {name} configuration"
            LOG.error(msg, exc_info=True)
            return Result(ResultType.FAILED, msg)

        return Result(ResultType.COMPLETED)


class ConfigureOpenStackNetworkAgentsLocalSettingsStep(BaseStep, JujuStepHelper):
    """Configure openstack-network-agents local settings via charm config.

    This is intended to run after microovn optional integrations are applied,
    so juju-info relation to openstack-network-agents exists.
    """

    def __init__(
        self,
        jhelper: JujuHelper,
        external_interface: str,
        bridge_name: str,
        physnet_name: str,
        model: str,
        enable_chassis_as_gw: bool = True,
    ):
        super().__init__(
            "Configure OpenStack network agents",
            "Setting openstack-network-agents local settings",
        )
        self.jhelper = jhelper
        self.model = model
        self.external_interface = external_interface
        self.bridge_name = bridge_name
        self.physnet_name = physnet_name
        self.enable_chassis_as_gw = enable_chassis_as_gw

    def is_skip(self, status: Status | None = None) -> Result:
        """Check if openstack-network-agents is deployed."""
        try:
            self.jhelper.get_application("openstack-network-agents", self.model)
        except Exception:
            return Result(ResultType.SKIPPED, "openstack-network-agents not deployed")
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run action to configure openstack-network-agents local settings."""
        try:
            self.jhelper.wait_application_ready(
                "openstack-network-agents",
                self.model,
                accepted_status=["active", "blocked", "waiting"],
                timeout=600,
            )

            unit_name = None

            try:
                principal = self.jhelper.get_leader_unit(
                    MICROOVN_APPLICATION, self.model
                )
            except Exception:
                app = self.jhelper.get_application(MICROOVN_APPLICATION, self.model)
                principal = None
                units_attr = getattr(app, "units", None)
                if units_attr:
                    try:
                        principal = next(iter(units_attr)).name
                    except Exception:
                        principal = next(iter(units_attr.keys()))

            if not principal or "/" not in principal:
                return Result(
                    ResultType.FAILED, "Could not determine principal microovn unit"
                )

            ordinal = principal.split("/", 1)[1]
            unit_name = f"openstack-network-agents/{ordinal}"

            LOG.debug(
                "Running set-network-agents-local-settings on %s"
                " (bridge=%s, physnet=%s, iface=%s, gw=%s)",
                unit_name,
                self.bridge_name,
                self.physnet_name,
                self.external_interface,
                self.enable_chassis_as_gw,
            )

            self.jhelper.run_action(
                unit_name,
                self.model,
                "set-network-agents-local-settings",
                action_params={
                    "external-interface": self.external_interface,
                    "bridge-name": self.bridge_name,
                    "physnet-name": self.physnet_name,
                    "enable-chassis-as-gw": self.enable_chassis_as_gw,
                },
            )

            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.debug(
                "Failed to configure openstack-network-agents via action: %s",
                e,
                exc_info=True,
            )
            return Result(
                ResultType.FAILED, "Failed to configure openstack-network-agents"
            )


def network_node_questions():
    return {
        "external_interface": sunbeam.core.questions.PromptQuestion(
            "External network's interface",
            description=(
                "Interface used by networking layer to allow remote access to cloud"
                " instances. This interface must be unconfigured"
                " (no IP address assigned) and connected to the external network."
            ),
        ),
    }


class LocalConfigureOpenStackNetworkAgentsStep(BaseStep):
    """Prompt for external interface (or use manifest) and configure agents."""

    def __init__(
        self,
        client: Client,
        node_name: str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
        bridge_name: str = "br-ex",
        physnet_name: str = "physnet1",
        enable_chassis_as_gw: bool = True,
    ):
        super().__init__(
            "Configure OpenStack network agents (local)",
            "Configuring openstack-network-agents local settings",
        )
        self.client = client
        self.node_name = node_name
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.external_interface: str | None = None
        self.bridge_name = bridge_name
        self.physnet_name = physnet_name
        self.enable_chassis_as_gw = enable_chassis_as_gw

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def _prompt_for_external_nic(self, console: Console | None) -> str:
        candidates: list[str] = []
        all_nics: list[dict] = []

        qbank = sunbeam.core.questions.QuestionBank(
            questions=network_node_questions(),
            console=console,
            accept_defaults=self.accept_defaults,
        )

        if not candidates:
            return qbank.external_interface.ask()

        while True:
            nic = qbank.external_interface.ask(
                new_default=candidates[0], new_choices=candidates
            )
            if not nic:
                continue
            state = next((i for i in all_nics if i.get("name") == nic), None) or {}
            if state.get("configured"):
                if not sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is configured. "
                    "Any configuration will be lost. Continue?"
                ).ask():
                    continue
            if state.get("up") and not state.get("connected"):
                if not sunbeam.core.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is not detected as connected. Continue?",
                    description="It may not work as expected.",
                ).ask():
                    continue
            return nic

    def prompt(self, console: Console | None = None, show_hint: bool = False) -> None:
        """Prompt for external interface if not provided in manifest."""
        if self.manifest and (ext := self.manifest.core.config.external_network):
            hostmap = (ext.nics or {}) if hasattr(ext, "nics") else {}
            nic = hostmap.get(self.node_name)
            if not nic and hasattr(ext, "nic"):
                nic = getattr(ext, "nic")
            if nic:
                self.external_interface = nic
                return
        self.external_interface = self._prompt_for_external_nic(console)

    def run(self, status: Status | None = None) -> Result:
        """Configure openstack-network-agents local settings via action."""
        if not self.external_interface:
            return Result(ResultType.FAILED, "No external interface selected")

        step = ConfigureOpenStackNetworkAgentsLocalSettingsStep(
            jhelper=self.jhelper,
            external_interface=self.external_interface,
            bridge_name=self.bridge_name,
            physnet_name=self.physnet_name,
            model=self.model,
            enable_chassis_as_gw=self.enable_chassis_as_gw,
        )
        return step.run(status)
