# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import abc
import hashlib
import ipaddress
import logging
import re
from abc import abstractmethod

from rich.console import Console

import sunbeam.core.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, Status, validate_ip_range
from sunbeam.core.juju import ActionFailedException, JujuHelper, JujuStepHelper
from sunbeam.core.manifest import Manifest
from sunbeam.steps import microovn

LOG = logging.getLogger(__name__)

CLOUD_CONFIG_SECTION = "CloudConfig"

EXT_NETWORK_DESCRIPTION = """\
Network from which the instances will be remotely \
accessed (outside OpenStack). Takes the form of a CIDR block.\
"""
EXT_NETWORK_RANGE_DESCRIPTION = """\
VMs intended to be accessed from remote hosts will \
be assigned dedicated addresses from a portion of the physical \
network (outside OpenStack). Takes the form of an IP range.\
"""
EXT_NETWORK_TYPE_DESCRIPTION = "Type of network to use for external access."
EXT_NETWORK_SEGMENTATION_ID_DESCRIPTION = "Vlan ID the external network is on."

# Do not allow special character, no consecutive '-' or '_'
# no leading or trailing '-' or '_', no leading digit
_NAMING_REGEX = r"^[a-zA-Z][a-zA-Z0-9]*(?:[-_][a-zA-Z0-9]+)*$"
_IFNAME_SIZE = 15  # Linux interface name max size


def _physnet_validation(value: str) -> None:
    if not value:
        raise ValueError("Physical network name cannot be empty")
    if len(value) > 64:
        # Arbitrary limit, not from any spec
        raise ValueError("Physical network name cannot exceed 64 characters")
    if not re.match(_NAMING_REGEX, value):
        raise ValueError("Invalid physical network name")


def user_questions():
    return {
        "run_demo_setup": sunbeam.core.questions.ConfirmQuestion(
            "Populate OpenStack cloud with demo user, default images, flavors etc",
            default_value=True,
            description=(
                "If enabled, demonstration resources will be created on the cloud."
            ),
        ),
        "username": sunbeam.core.questions.PromptQuestion(
            "Username to use for access to OpenStack",
            default_value="demo",
            description="Username for the demonstration user.",
        ),
        "password": sunbeam.core.questions.PasswordPromptQuestion(
            "Password to use for access to OpenStack",
            default_function=utils.generate_password,
            password=True,
            description="Password for the demonstration user.",
        ),
        "cidr": sunbeam.core.questions.PromptQuestion(
            "Project network",
            default_value="192.168.0.0/24",
            validation_function=ipaddress.ip_network,
            description=(
                "Network range for the private network for the demonstration user's"
                " project. Typically an unroutable network (RFC 1918)."
            ),
        ),
        "nameservers": sunbeam.core.questions.PromptQuestion(
            "Project network's nameservers",
            default_function=lambda: " ".join(utils.get_nameservers()),
            description=(
                "A list of DNS server IP addresses (comma separated)"
                " that should be used for external DNS resolution from"
                " cloud instances. If not specified, the system's default"
                " nameservers will be used."
            ),
        ),
        "security_group_rules": sunbeam.core.questions.ConfirmQuestion(
            "Enable ping and SSH access to instances?",
            default_value=True,
            description=(
                "If enabled, security groups will be created with"
                " rules to allow ICMP and SSH access to instances."
            ),
        ),
        "remote_access_location": sunbeam.core.questions.PromptQuestion(
            "Local or remote access to VMs",
            choices=[utils.LOCAL_ACCESS, utils.REMOTE_ACCESS],
            default_value=utils.LOCAL_ACCESS,
            description=(
                "VMs will be accessible only from the local host"
                " or only from remote hosts. For remote, you must"
                " specify the network interface dedicated to VM"
                " access traffic. The intended remote hosts must"
                " have connectivity to this interface."
            ),
        ),
        "physnet": sunbeam.core.questions.PromptQuestion(
            "Project external network name",
            description=(
                "External network providing floating IPs for the demonstration"
                " user's project."
            ),
        ),
    }


def ext_net_questions():
    return {
        "cidr": sunbeam.core.questions.PromptQuestion(
            "External network",
            validation_function=ipaddress.ip_network,
            description=EXT_NETWORK_DESCRIPTION,
        ),
        "gateway": sunbeam.core.questions.PromptQuestion(
            "External network's gateway",
            default_value=None,
            validation_function=ipaddress.ip_address,
            description="Router IP address connecting the network for outside use.",
        ),
        "range": sunbeam.core.questions.PromptQuestion(
            "External network's allocation range",
            default_value=None,
            validation_function=validate_ip_range,
            description=EXT_NETWORK_RANGE_DESCRIPTION,
        ),
        "network_type": sunbeam.core.questions.PromptQuestion(
            "External network's type [flat/vlan]",
            choices=["flat", "vlan"],
            default_value="flat",
            description=EXT_NETWORK_TYPE_DESCRIPTION,
        ),
        "segmentation_id": sunbeam.core.questions.PromptQuestion(
            "External network's segmentation id",
            default_value=0,
            description=EXT_NETWORK_SEGMENTATION_ID_DESCRIPTION,
        ),
    }


def physical_network_question() -> dict[str, sunbeam.core.questions.Question]:
    return {
        "physnet_name": sunbeam.core.questions.PromptQuestion(
            "External network's physical network name",
            description="Physical network name for the external network.",
            validation_function=_physnet_validation,
        ),
        "configure_more": sunbeam.core.questions.ConfirmQuestion(
            "Do you want to configure another external network?",
            default_value=False,
        ),
    }


def ext_net_questions_local_only():
    return {
        "cidr": sunbeam.core.questions.PromptQuestion(
            "External network - arbitrary but must not be in use",
            validation_function=ipaddress.ip_network,
            description=EXT_NETWORK_DESCRIPTION,
        ),
        "range": sunbeam.core.questions.PromptQuestion(
            "External network's allocation range",
            default_value=None,
            validation_function=validate_ip_range,
            description=EXT_NETWORK_RANGE_DESCRIPTION,
        ),
        "network_type": sunbeam.core.questions.PromptQuestion(
            "External network's type [flat/vlan]",
            choices=["flat", "vlan"],
            default_value="flat",
            description=EXT_NETWORK_TYPE_DESCRIPTION,
        ),
        "segmentation_id": sunbeam.core.questions.PromptQuestion(
            "External network's segmentation id",
            default_value=0,
            description=EXT_NETWORK_SEGMENTATION_ID_DESCRIPTION,
        ),
    }


class BaseUserQuestions(BaseStep):
    """Base class for user configuration questions."""

    def __init__(
        self,
        client: Client,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(
            "Collect cloud configuration", "Collecting cloud configuration"
        )
        self.client = client
        self.accept_defaults = accept_defaults
        self.manifest = manifest
        self.variables: dict = {}

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    @abstractmethod
    def _get_question_bank(
        self,
        console: Console | None,
        preseed: dict,
        show_hint: bool,
    ) -> sunbeam.core.questions.QuestionBank:
        """Return the question bank to use."""
        raise NotImplementedError

    @abstractmethod
    def _configure_remote_access(
        self,
        user_bank: sunbeam.core.questions.QuestionBank,
    ) -> None:
        """Configure remote access location."""

    def _prompt_external_networks(
        self,
        variables: dict,
        preseed: dict,
        questions: dict,
        console: Console | None,
        accept_defaults: bool,
        show_hint: bool,
        configure_multiple: bool,
    ) -> dict:
        """Prompt the user for external network configuration.

        Preseed is a dict of physnet to network configuration.
        """
        current = 0
        seen_physnet: list[str] = []
        known_physnet = sorted({*preseed.keys(), *variables["external_network"].keys()})

        physnet_bank = sunbeam.core.questions.QuestionBank(
            questions=physical_network_question(),
            console=console,
            preseed=preseed,
            previous_answers={},
            accept_defaults=accept_defaults,
            show_hint=show_hint,
        )
        while True:
            physnet = None
            candidate = None
            while not candidate:
                if known_physnet:
                    new = known_physnet.pop(0)
                else:
                    new = f"physnet{current + 1}"
                if new not in seen_physnet:
                    candidate = new
            while not physnet or physnet in seen_physnet:
                if physnet in preseed or self.accept_defaults:
                    physnet = candidate
                else:
                    physnet = physnet_bank.physnet_name.ask(new_default=candidate)
            seen_physnet.append(physnet)
            ext_net_bank = sunbeam.core.questions.QuestionBank(
                questions=questions,
                console=console,
                preseed=preseed.get(physnet, {}),
                previous_answers=variables["external_network"].get(physnet, {}),
                accept_defaults=accept_defaults,
                show_hint=show_hint,
            )

            next_cidr_candidate = f"172.16.{min(2 + current, 254)}.0/24"
            variables["external_network"].setdefault(physnet, {})
            variables["external_network"][physnet]["physical_network"] = physnet
            variables["external_network"][physnet]["cidr"] = ext_net_bank.cidr.ask(
                new_default=next_cidr_candidate
            )

            external_network = ipaddress.ip_network(
                variables["external_network"][physnet]["cidr"]
            )
            external_network_hosts = list(external_network.hosts())
            default_gateway = variables["external_network"][physnet].get(
                "gateway"
            ) or str(external_network_hosts[0])
            if variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
                variables["external_network"][physnet]["gateway"] = default_gateway
            else:
                variables["external_network"][physnet]["gateway"] = (
                    ext_net_bank.gateway.ask(new_default=default_gateway)
                )

            default_allocation_range = (
                variables["external_network"][physnet].get("range")
                or f"{external_network_hosts[1]}-{external_network_hosts[-1]}"
            )
            variables["external_network"][physnet]["range"] = ext_net_bank.range.ask(
                new_default=default_allocation_range
            )

            variables["external_network"][physnet]["network_type"] = (
                ext_net_bank.network_type.ask()
            )
            if variables["external_network"][physnet]["network_type"] == "vlan":
                variables["external_network"][physnet]["segmentation_id"] = (
                    ext_net_bank.segmentation_id.ask()
                )
            else:
                variables["external_network"][physnet]["segmentation_id"] = 0
            current += 1
            can_configure_more = len(known_physnet) > 0 and configure_multiple
            if self.accept_defaults and can_configure_more:
                continue
            if not physnet_bank.configure_more.ask(new_default=can_configure_more):
                break
        return variables

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Prompt the user for basic cloud configuration.

        Prompts the user for required information for cloud configuration.

        :param console: the console to prompt on
        :type console: rich.console.Console (Optional)
        """
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        for section in ["user", "external_network"]:
            if not self.variables.get(section):
                self.variables[section] = {}
        preseed = {}
        if self.manifest and (user := self.manifest.core.config.user):
            preseed = user.model_dump(by_alias=True)

        user_bank = self._get_question_bank(console, preseed, show_hint)
        self._configure_remote_access(user_bank)

        # External Network Configuration
        preseed = {}
        if self.manifest:
            if ext_networks := self.manifest.core.config.external_networks:
                # Preseed with first external network
                preseed = {
                    physnet: network.model_dump(by_alias=True)
                    for physnet, network in ext_networks.items()
                }
            elif ext_network := self.manifest.core.config.external_network:
                # Using deprecated single external_network field
                LOG.warning(
                    "Manifest uses deprecated 'external_network' field, please "
                    "update to 'external-networks'"
                )
                preseed = {"physnet1": ext_network.model_dump(by_alias=True)}

        # Let's convert the old external_network to new
        ext_net = self.variables.get("external_network")
        if isinstance(ext_net, dict) and not all(
            isinstance(item, dict) for item in ext_net.values()
        ):
            self.variables["external_network"] = {"physnet1": ext_net}

        if self.variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
            questions = ext_net_questions_local_only()
            configure_multiple = False
        else:
            questions = ext_net_questions()
            configure_multiple = True

        self.variables = self._prompt_external_networks(
            self.variables,
            preseed,
            questions,
            console,
            self.accept_defaults,
            show_hint,
            configure_multiple,
        )

        self.variables["user"]["run_demo_setup"] = user_bank.run_demo_setup.ask()
        if self.variables["user"]["run_demo_setup"]:
            # User configuration
            self.variables["user"]["username"] = user_bank.username.ask()
            self.variables["user"]["password"] = user_bank.password.ask()
            self.variables["user"]["cidr"] = user_bank.cidr.ask()
            physnets = sorted(self.variables["external_network"].keys())
            self.variables["user"]["physnet"] = user_bank.physnet.ask(
                new_choices=physnets, new_default=physnets[0]
            )
            nameservers = user_bank.nameservers.ask()
            self.variables["user"]["dns_nameservers"] = (
                nameservers.split() if nameservers else []
            )
            self.variables["user"]["security_group_rules"] = (
                user_bank.security_group_rules.ask()
            )

        sunbeam.core.questions.write_answers(
            self.client, CLOUD_CONFIG_SECTION, self.variables
        )

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion."""
        return Result(ResultType.COMPLETED)


class UnitGetterMixin(abc.ABC):
    APP: str
    client: Client
    jhelper: JujuHelper
    model: str

    @abstractmethod
    def get_unit(self, name: str) -> str:
        """Get the juju unit for the given node name."""
        raise NotImplementedError


class PrincipalUnitGetterMixin(UnitGetterMixin):
    def get_unit(self, name: str) -> str:
        """Get the juju unit for the given machine name."""
        node = self.client.cluster.get_node_info(name)
        machine_id = str(node.get("machineid"))
        unit = self.jhelper.get_unit_from_machine(self.APP, machine_id, self.model)
        return unit


class OpenstackNetworkAgentsUnitGetterMixin(UnitGetterMixin, JujuStepHelper):
    def get_unit(self, name: str) -> str:
        """Get the juju unit for the given network agent name."""
        node = self.client.cluster.get_node_info(name)
        machine_id = str(node.get("machineid"))
        principal = self.jhelper.get_unit_from_machine(
            microovn.APPLICATION, machine_id, self.model
        )
        unit_name = self.find_subordinate_unit_for(principal, self.APP, self.model)
        return unit_name


class SetExternalNetworkUnitsOptionsStep(BaseStep, UnitGetterMixin):
    APP: str
    DISPLAY_NAME: str
    ACTION: str

    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        msg: str | None = None,
        description: str | None = None,
    ):
        if not msg:
            msg = f"Apply {self.DISPLAY_NAME} settings"
        if not description:
            description = f"Applying {self.DISPLAY_NAME} settings"
        super().__init__(msg, description)
        self.client = client
        if isinstance(names, str):
            names = [names]
        self.names = names
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.bridge_mappings: dict[str, str | None] = {}

    def _create_bridge_name(self, physnet: str) -> str:
        """Create bridge name from physnet name.

        A bridge name must be at most 15 characters long.
        """
        if len(physnet) <= 12:
            return f"br-{physnet}"
        else:
            return (
                f"br-{physnet[:7]}-{hashlib.sha256(physnet.encode()).hexdigest()[:4]}"
            )

    def _build_bridge_mapping(self, physnet_mapping: list[tuple[str, str]]) -> str:
        """Build bridge mapping string from physnet to nic mapping."""
        mapping_parts = []
        # Sort by physnet name to have a deterministic order
        for physnet, nic in sorted(physnet_mapping, key=lambda x: x[0]):
            mapping_parts.append(f"{self._create_bridge_name(physnet)}:{physnet}:{nic}")
        return " ".join(mapping_parts)

    def run(self, status: Status | None = None) -> Result:
        """Apply individual unit settings."""
        for name in self.names:
            self.update_status(status, f"setting configuration for {name}")
            bridge_mapping = self.bridge_mappings.get(name)
            if bridge_mapping is None:
                LOG.debug(f"No NIC found for {name}, skipping.")
                continue
            node = self.client.cluster.get_node_info(name)
            self.machine_id = str(node.get("machineid"))
            unit = self.get_unit(name)
            try:
                self.jhelper.run_action(
                    unit,
                    self.model,
                    self.ACTION,
                    action_params={
                        "bridge-mapping": bridge_mapping,
                    },
                )
            except (ActionFailedException, TimeoutError):
                _message = f"Unable to set {name!r} configuration"
                LOG.debug(_message, exc_info=True)
                return Result(ResultType.FAILED, _message)
        return Result(ResultType.COMPLETED)
