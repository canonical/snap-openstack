# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import json
import logging
import os
from pathlib import Path

import click
from rich.console import Console

import sunbeam.core.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, Status, validate_ip_range
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)

CLOUD_CONFIG_SECTION = "CloudConfig"
PCI_CONFIG_SECTION = "PCI"
LOG = logging.getLogger(__name__)
console = Console()

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
            # This is not true
            description=(
                "VMs will be accessible only from the local host"
                " or only from remote hosts. For remote, you must"
                " specify the network interface dedicated to VM"
                " access traffic. The intended remote hosts must"
                " have connectivity to this interface."
            ),
        ),
    }


def ext_net_questions():
    return {
        "cidr": sunbeam.core.questions.PromptQuestion(
            "External network",
            default_value="172.16.2.0/24",
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


def ext_net_questions_local_only():
    return {
        "cidr": sunbeam.core.questions.PromptQuestion(
            "External network - arbitrary but must not be in use",
            default_value="172.16.2.0/24",
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


VARIABLE_DEFAULTS: dict[str, dict[str, str | int | bool | None]] = {
    "user": {
        "username": "demo",
        "cidr": "192.168.0.0/24",
        "security_group_rules": True,
    },
    "external_network": {
        "cidr": "172.16.2.0/24",
        "gateway": None,
        "range": None,
        "physical_network": "physnet1",
        "network_type": "flat",
        "segmentation_id": 0,
    },
}


def retrieve_admin_credentials(jhelper: JujuHelper, model: str) -> dict:
    """Retrieve cloud admin credentials.

    Retrieve cloud admin credentials from keystone and
    return as a dict suitable for use with subprocess
    commands.  Variables are prefixed with OS_.
    """
    app = "keystone"
    action_cmd = "get-admin-account"

    try:
        unit = jhelper.get_leader_unit(app, model)
    except LeaderNotFoundException:
        raise click.ClickException(f"Unable to get {app} leader")

    try:
        action_result = jhelper.run_action(unit, model, action_cmd)
    except (ActionFailedException, TimeoutError) as e:
        LOG.debug(f"Running action {action_cmd} on {unit} failed: {str(e)}")
        raise click.ClickException("Unable to retrieve openrc from Keystone service")

    params = {
        "OS_USERNAME": action_result.get("username"),
        "OS_PASSWORD": action_result.get("password"),
        "OS_AUTH_URL": action_result.get("public-endpoint"),
        "OS_USER_DOMAIN_NAME": action_result.get("user-domain-name"),
        "OS_PROJECT_DOMAIN_NAME": action_result.get("project-domain-name"),
        "OS_PROJECT_NAME": action_result.get("project-name"),
        "OS_AUTH_VERSION": action_result.get("api-version"),
        "OS_IDENTITY_API_VERSION": action_result.get("api-version"),
    }

    action_cmd = "list-ca-certs"
    try:
        action_result = jhelper.run_action(unit, model, action_cmd)
    except ActionFailedException as e:
        LOG.debug(f"Running action {action_cmd} on {unit} failed: {str(e)}")
        raise click.ClickException("Unable to retrieve CA certs from Keystone service")

    ca_bundle = []
    for name, certs in action_result.items():
        # certs = json.loads(certs)
        ca = certs.get("ca")
        chain = certs.get("chain")
        if ca and ca not in ca_bundle:
            ca_bundle.append(ca)
        if chain and chain not in ca_bundle:
            ca_bundle.append(chain)

    bundle = "\n".join(ca_bundle)

    if bundle:
        home = os.environ["SNAP_REAL_HOME"]
        cafile = Path(home) / ".config" / "openstack" / "ca_bundle.pem"
        LOG.debug("Writing CA bundle to {str(cafile)}")

        cafile.parent.mkdir(mode=0o775, parents=True, exist_ok=True)
        if not cafile.exists():
            cafile.touch()
        cafile.chmod(0o660)

        with cafile.open("w") as file:
            file.write(bundle)

        params["OS_CACERT"] = str(cafile)

    return params


def get_external_network_configs(client: Client) -> dict:
    charm_config = {}

    variables = sunbeam.core.questions.load_answers(client, CLOUD_CONFIG_SECTION)
    ext_network = variables.get("external_network", {})
    charm_config["external-bridge"] = "br-ex"
    if (
        variables.get("user", {}).get("remote_access_location", "")
        == utils.LOCAL_ACCESS
    ):
        external_network = ipaddress.ip_network(
            variables.get("external_network", {}).get("cidr")
        )
        bridge_interface = f"{ext_network.get('gateway')}/{external_network.prefixlen}"
        charm_config["external-bridge-address"] = bridge_interface
    else:
        charm_config["external-bridge-address"] = utils.IPVANYNETWORK_UNSET

    charm_config["physnet-name"] = variables.get("external_network", {}).get(
        "physical_network", VARIABLE_DEFAULTS["external_network"]["physical_network"]
    )
    return charm_config


def get_pci_whitelist_config(client: Client) -> dict:
    charm_config = {}
    variables = sunbeam.core.questions.load_answers(client, PCI_CONFIG_SECTION)
    charm_config["pci-device-specs"] = json.dumps(variables.get("pci_whitelist", "[]"))
    return charm_config


class UserOpenRCStep(BaseStep):
    """Generate openrc for created cloud user."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        auth_url: str,
        auth_version: str,
        cacert: str | None = None,
        openrc: Path | None = None,
    ):
        super().__init__(
            "Generate admin openrc", "Generating openrc for cloud admin usage"
        )
        self.client = client
        self.tfhelper = tfhelper
        self.auth_url = auth_url
        self.auth_version = auth_version
        self.cacert = cacert
        self.openrc = openrc

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if "user" not in self.variables:
            LOG.debug("Demo setup not yet done")
            return Result(ResultType.SKIPPED)
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Fetch openrc from terraform state."""
        try:
            tf_output = self.tfhelper.output(hide_output=True)
            # Mask any passwords before printing process.stdout
            self._print_openrc(tf_output)
            return Result(ResultType.COMPLETED)
        except TerraformException as e:
            LOG.exception("Error getting terraform output")
            return Result(ResultType.FAILED, str(e))

    def _print_openrc(self, tf_output: dict) -> None:
        """Print openrc to console and save to disk using provided information."""
        _openrc = f"""# openrc for {tf_output["OS_USERNAME"]}
export OS_AUTH_URL={self.auth_url}
export OS_USERNAME={tf_output["OS_USERNAME"]}
export OS_PASSWORD={tf_output["OS_PASSWORD"]}
export OS_USER_DOMAIN_NAME={tf_output["OS_USER_DOMAIN_NAME"]}
export OS_PROJECT_DOMAIN_NAME={tf_output["OS_PROJECT_DOMAIN_NAME"]}
export OS_PROJECT_NAME={tf_output["OS_PROJECT_NAME"]}
export OS_AUTH_VERSION={self.auth_version}
export OS_IDENTITY_API_VERSION={self.auth_version}"""
        if self.cacert:
            _openrc = f"{_openrc}\nexport OS_CACERT={self.cacert}"
        if self.openrc:
            message = f"Writing openrc to {self.openrc} ... "
            console.status(message)
            with self.openrc.open("w") as f_openrc:
                os.fchmod(f_openrc.fileno(), mode=0o640)
                f_openrc.write(_openrc)
            console.print(f"{message}[green]done[/green]")
        else:
            console.print(_openrc)


class UserQuestions(BaseStep):
    """Ask user configuration questions."""

    def __init__(
        self,
        client: Client,
        answer_file: Path,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(
            "Collect cloud configuration", "Collecting cloud configuration"
        )
        self.client = client
        self.accept_defaults = accept_defaults
        self.manifest = manifest
        self.answer_file = answer_file

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

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

        user_bank = sunbeam.core.questions.QuestionBank(
            questions=user_questions(),
            console=console,
            preseed=preseed,
            previous_answers=self.variables.get("user"),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        self.variables["user"]["remote_access_location"] = (
            user_bank.remote_access_location.ask()
        )
        # External Network Configuration
        preseed = {}
        if self.manifest and (
            ext_network := self.manifest.core.config.external_network
        ):
            preseed = ext_network.model_dump(by_alias=True)
        if self.variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
            ext_net_bank = sunbeam.core.questions.QuestionBank(
                questions=ext_net_questions_local_only(),
                console=console,
                preseed=preseed,
                previous_answers=self.variables.get("external_network"),
                accept_defaults=self.accept_defaults,
                show_hint=show_hint,
            )
        else:
            ext_net_bank = sunbeam.core.questions.QuestionBank(
                questions=ext_net_questions(),
                console=console,
                preseed=preseed,
                previous_answers=self.variables.get("external_network"),
                accept_defaults=self.accept_defaults,
                show_hint=show_hint,
            )
        self.variables["external_network"]["cidr"] = ext_net_bank.cidr.ask()
        external_network = ipaddress.ip_network(
            self.variables["external_network"]["cidr"]
        )
        external_network_hosts = list(external_network.hosts())
        default_gateway = self.variables["external_network"].get("gateway") or str(
            external_network_hosts[0]
        )
        if self.variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
            self.variables["external_network"]["gateway"] = default_gateway
        else:
            self.variables["external_network"]["gateway"] = ext_net_bank.gateway.ask(
                new_default=default_gateway
            )

        default_allocation_range = (
            self.variables["external_network"].get("range")
            or f"{external_network_hosts[1]}-{external_network_hosts[-1]}"
        )
        self.variables["external_network"]["range"] = ext_net_bank.range.ask(
            new_default=default_allocation_range
        )

        self.variables["external_network"]["physical_network"] = VARIABLE_DEFAULTS[
            "external_network"
        ]["physical_network"]

        self.variables["external_network"]["network_type"] = (
            ext_net_bank.network_type.ask()
        )
        if self.variables["external_network"]["network_type"] == "vlan":
            self.variables["external_network"]["segmentation_id"] = (
                ext_net_bank.segmentation_id.ask()
            )
        else:
            self.variables["external_network"]["segmentation_id"] = 0

        self.variables["user"]["run_demo_setup"] = user_bank.run_demo_setup.ask()
        if self.variables["user"]["run_demo_setup"]:
            # User configuration
            self.variables["user"]["username"] = user_bank.username.ask()
            self.variables["user"]["password"] = user_bank.password.ask()
            self.variables["user"]["cidr"] = user_bank.cidr.ask()
            nameservers = user_bank.nameservers.ask()
            self.variables["user"]["dns_nameservers"] = (
                nameservers.split() if nameservers else None
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


class DemoSetup(BaseStep):
    """Default cloud configuration for all-in-one install."""

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        answer_file: Path,
    ):
        super().__init__(
            "Create demonstration configuration",
            "Creating demonstration user, project and networking",
        )
        self.answer_file = answer_file
        self.tfhelper = tfhelper
        self.client = client

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        self.tfhelper.write_tfvars(self.variables, self.answer_file)
        try:
            self.tfhelper.apply()
            return Result(ResultType.COMPLETED)
        except TerraformException as e:
            LOG.exception("Error configuring cloud")
            return Result(ResultType.FAILED, str(e))


class TerraformDemoInitStep(TerraformInitStep):
    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
    ):
        super().__init__(tfhelper)
        self.tfhelper = tfhelper
        self.client = client

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)


class SetHypervisorUnitsOptionsStep(BaseStep):
    def __init__(
        self,
        client: Client,
        names: list[str] | str,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        msg: str = "Apply hypervisor settings",
        description: str = "Applying hypervisor settings",
    ):
        super().__init__(msg, description)
        self.client = client
        if isinstance(names, str):
            names = [names]
        self.names = names
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.nics: dict[str, str | None] = {}

    def run(self, status: Status | None = None) -> Result:
        """Apply individual hypervisor settings."""
        app = "openstack-hypervisor"
        action_cmd = "set-hypervisor-local-settings"
        for name in self.names:
            self.update_status(status, f"setting hypervisor configuration for {name}")
            nic = self.nics.get(name)
            if nic is None:
                LOG.debug(f"No NIC found for hypervisor {name}, skipping.")
                continue
            node = self.client.cluster.get_node_info(name)
            self.machine_id = str(node.get("machineid"))
            unit = self.jhelper.get_unit_from_machine(app, self.machine_id, self.model)
            try:
                self.jhelper.run_action(
                    unit,
                    self.model,
                    action_cmd,
                    action_params={
                        "external-nic": nic,
                    },
                )
            except (ActionFailedException, TimeoutError):
                _message = f"Unable to set hypervisor {name!r} configuration"
                LOG.debug(_message, exc_info=True)
                return Result(ResultType.FAILED, _message)
        return Result(ResultType.COMPLETED)


def _sorter(cmd: tuple[str, click.Command]) -> int:
    if cmd[0] == "deployment":
        return 0
    return 1


def _keep_cmd_params(cmd: click.Command, params: dict) -> dict:
    """Keep parameters from parent context that are in the command."""
    out_params = {}
    for param in cmd.params:
        if param.name in params:
            out_params[param.name] = params[param.name]
    return out_params


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-m",
    "--manifest",
    "manifest_path",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--openrc",
    help="Output file for cloud access details.",
    type=click.Path(dir_okay=False, path_type=Path),
)
def configure(
    ctx: click.Context,
    openrc: Path | None = None,
    manifest_path: Path | None = None,
    accept_defaults: bool = False,
) -> None:
    """Configure cloud with some sensible defaults."""
    if ctx.invoked_subcommand is not None:
        return
    commands = sorted(configure.commands.items(), key=_sorter)
    for name, command in commands:
        LOG.debug("Running configure %r", name)
        cmd_ctx = click.Context(
            command,
            parent=ctx,
            info_name=command.name,
            allow_extra_args=True,
        )
        cmd_ctx.params = _keep_cmd_params(command, ctx.params)
        cmd_ctx.forward(command)
