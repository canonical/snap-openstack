# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import json
import logging
import os
import typing
from pathlib import Path

import click
from rich.console import Console

import sunbeam.core.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, Status
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
from sunbeam.steps.configure import CLOUD_CONFIG_SECTION

PCI_CONFIG_SECTION = "PCI"
DPDK_CONFIG_SECTION = "DPDK"

LOG = logging.getLogger(__name__)
console = Console()


def dpdk_questions():
    return {
        "enabled": sunbeam.core.questions.ConfirmQuestion(
            "Enable and configure DPDK",
            default_value=False,
            description=(
                "Enable OVS DPDK data path, handling packets in userspace. It provides "
                "improved performance compared to the standard OVS kernel data path. "
                "DPDK capable network interfaces are required."
            ),
        ),
        "datapath_cores": sunbeam.core.questions.PromptQuestion(
            "The number of cores allocated to OVS datapath processing",
            default_value="1",
            description=(
                "The specified number of cores will be allocated to OVS datapath "
                "processing, taking into account the NUMA location of physical "
                "DPDK ports. Isolated cpu cores must be preconfigured using kernel "
                "parameters."
            ),
        ),
        "control_plane_cores": sunbeam.core.questions.PromptQuestion(
            "The number of cores allocated to OVS control plane processing",
            default_value="1",
            description=(
                "The specified number of cores will be allocated to OVS control "
                "plane processing, taking into account the NUMA location of physical "
                "DPDK ports. Isolated cpu cores must be preconfigured using kernel "
                "parameters."
            ),
        ),
        "memory": sunbeam.core.questions.PromptQuestion(
            "The amount of memory in MB allocated to OVS from huge pages",
            default_value="1024",
            description=(
                "The total amount of memory in MB to allocate from huge pages for OVS "
                "DPDK. The memory will be distributed across NUMA nodes based on the "
                "location of the physical DPDK ports. Currently uses 1GB pages, make "
                "sure to specify a multiple of 1024 and preallocate enough 1GB pages."
            ),
        ),
        "driver": sunbeam.core.questions.PromptQuestion(
            "The DPDK-compatible driver used for DPDK physical ports",
            default_value="vfio-pci",
        ),
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
    if (
        variables.get("user", {}).get("remote_access_location", "")
        == utils.LOCAL_ACCESS
    ):
        # In local access, we only support a single external network
        # get the first one
        # Check if ext_network is nested (dict of dict) or flat (single dict)
        first_key = next(iter(ext_network)) if ext_network else None
        if first_key and isinstance(ext_network.get(first_key), dict):
            # Nested structure like {"asd": {"cidr": ""}}
            ext_network = ext_network.get(first_key)
        # else: already a flat dict like {"cidr": ""}

        external_network = ipaddress.ip_network(ext_network.get("cidr"))
        bridge_interface = f"{ext_network.get('gateway')}/{external_network.prefixlen}"
        charm_config["external-bridge-address"] = bridge_interface
    else:
        charm_config["external-bridge-address"] = utils.IPVANYNETWORK_UNSET

    return charm_config


def get_pci_whitelist_config(client: Client) -> dict:
    charm_config = {}
    variables = sunbeam.core.questions.load_answers(client, PCI_CONFIG_SECTION)
    charm_config["pci-device-specs"] = json.dumps(variables.get("pci_whitelist", []))
    return charm_config


def get_dpdk_config(client: Client) -> dict:
    charm_config = {}
    variables = sunbeam.core.questions.load_answers(client, DPDK_CONFIG_SECTION)
    charm_config["dpdk-enabled"] = variables.get("enabled", False)
    charm_config["dpdk-datapath-cores"] = int(variables.get("datapath_cores") or 0)
    charm_config["dpdk-control-plane-cores"] = int(
        variables.get("control_plane_cores") or 0
    )
    charm_config["dpdk-memory"] = int(variables.get("memory") or 0)
    charm_config["dpdk-driver"] = variables.get("driver") or "vfio-pci"
    charm_config["dpdk-memory"] = int(variables.get("memory") or 0)
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


class BaseConfigDPDKStep(BaseStep):
    """Prompt the user for DPDK configuration.

    Subclasses are expected to provide the dpdk port list based on the
    deployment type (local or maas).
    """

    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("DPDK Settings", "Configure DPDK")
        self.client = client
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

        self.nics: typing.Any = None

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def _prompt_nics(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        pass

    def _get_dpdk_manifest_config(self) -> typing.Any:
        if not self.manifest:
            return None
        return self.manifest.core.config.dpdk

    def _get_dpdk_manifest_ports(self) -> dict:
        dpdk_manifest_config = self._get_dpdk_manifest_config()
        if dpdk_manifest_config and dpdk_manifest_config.ports:
            return dpdk_manifest_config.ports
        return {}

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Prompt the user for DPDK configuration."""
        self.variables = sunbeam.core.questions.load_answers(
            self.client, DPDK_CONFIG_SECTION
        )
        preseed = {}
        if self.manifest and (dpdk := self.manifest.core.config.dpdk):
            preseed = dpdk.model_dump(by_alias=True)

        dpdk_bank = sunbeam.core.questions.QuestionBank(
            questions=dpdk_questions(),
            console=console,
            preseed=preseed,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )

        self.variables["enabled"] = dpdk_bank.enabled.ask()
        if not self.variables["enabled"]:
            LOG.debug("DPDK disabled.")
        else:
            self._prompt_nics(console, show_hint)

            self.variables["datapath_cores"] = dpdk_bank.datapath_cores.ask()
            self.variables["control_plane_cores"] = dpdk_bank.control_plane_cores.ask()
            self.variables["memory"] = dpdk_bank.memory.ask()
            if int(self.variables["memory"] or 0) % 1024:
                raise click.ClickException(
                    "DPDK uses 1GB huge pages, please specify a multple of 1024. "
                    "Received: %s (MB)." % self.variables["memory"]
                )
            self.variables["driver"] = dpdk_bank.driver.ask()

        sunbeam.core.questions.write_answers(
            self.client, DPDK_CONFIG_SECTION, self.variables
        )

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion."""
        return Result(ResultType.COMPLETED)

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        if not self.nics:
            return Result(ResultType.SKIPPED)
        else:
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
