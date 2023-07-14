# Copyright (c) 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import ipaddress
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, TextIO

import click
from rich.console import Console
from rich.prompt import InvalidResponse, PromptBase
from snaphelpers import Snap

import sunbeam.jobs.questions
from sunbeam import utils
from sunbeam.clusterd.client import Client
from sunbeam.commands.juju import JujuLoginStep
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.commands.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformInitStep,
)
from sunbeam.jobs.checks import DaemonGroupCheck, VerifyBootstrappedCheck
from sunbeam.jobs.common import (
    BaseStep,
    Result,
    ResultType,
    Status,
    run_plan,
    run_preflight_checks,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    ModelNotFoundException,
    run_sync,
)

CLOUD_CONFIG_SECTION = "CloudConfig"
LOG = logging.getLogger(__name__)
console = Console()


class NicPrompt(PromptBase[str]):
    """A prompt that asks for a NIC on the local machine and validates it.

    Unlike other questions this prompt validates the users choice and if it
    fails validation the user has an oppertunity to fix any issue in another
    session and continue without exiting from the prompt.
    """

    response_type = str
    validate_error_message = "[prompt.invalid]Please valid nic"

    def check_choice(self, value: str) -> bool:
        """Validate the choice of nic."""
        nics = utils.get_free_nics(include_configured=True)
        try:
            value = value.strip().lower()
        except AttributeError:
            # Likely an empty string has been returned.
            raise InvalidResponse(f"\n'{value}' not a valid nic name")
        if value not in nics:
            raise InvalidResponse(f"\n'{value}' not found")
        return True

    def __call__(self, *, default: Any = ..., stream: Optional[TextIO] = None) -> Any:
        """Run the prompt loop.

        Args:
            default (Any, optional): Optional default value.

        Returns:
            PromptType: Processed value.
        """
        while True:
            # Limit options displayed to user to unconfigured nics.
            self.choices = utils.get_free_nics(include_configured=False)
            # Assume that if a default has been passed in and it is configured it is
            # probably the right one. The user will be prompted to confirm later.
            if not default or default not in utils.get_free_nics(
                include_configured=True
            ):
                if len(self.choices) > 0:
                    default = self.choices[0]
            self.pre_prompt()
            prompt = self.make_prompt(default)
            value = self.get_input(self.console, prompt, password=False, stream=stream)
            if value == "":
                if default:
                    # Unlike super.__call__ do not return here as we still need to
                    # validate the choice.
                    value = default
                else:
                    self.console.print("\nInvalid nic")
                    continue
            try:
                return_value = self.process_response(value)
            except InvalidResponse as error:
                self.on_validate_error(value, error)
                continue
            else:
                return return_value


class NicQuestion(sunbeam.jobs.questions.Question):
    """Ask the user a simple yes / no question."""

    @property
    def question_function(self):
        return NicPrompt.ask


def user_questions():
    return {
        "run_demo_setup": sunbeam.jobs.questions.ConfirmQuestion(
            "Populate OpenStack cloud with demo user, default images, flavors etc",
            default_value=True,
        ),
        "username": sunbeam.jobs.questions.PromptQuestion(
            "Username to use for access to OpenStack", default_value="demo"
        ),
        "password": sunbeam.jobs.questions.PasswordPromptQuestion(
            "Password to use for access to OpenStack",
            default_function=utils.generate_password,
            password=True,
        ),
        "cidr": sunbeam.jobs.questions.PromptQuestion(
            "Network range to use for project network",
            default_value="192.168.122.0/24",
            validation_function=ipaddress.ip_network,
        ),
        "nameservers": sunbeam.jobs.questions.PromptQuestion(
            "List of nameservers guests should use for DNS resolution",
            default_function=lambda: " ".join(utils.get_nameservers()),
        ),
        "security_group_rules": sunbeam.jobs.questions.ConfirmQuestion(
            "Enable ping and SSH access to instances?", default_value=True
        ),
        "remote_access_location": sunbeam.jobs.questions.PromptQuestion(
            "Local or remote access to VMs",
            choices=[utils.LOCAL_ACCESS, utils.REMOTE_ACCESS],
            default_value=utils.LOCAL_ACCESS,
        ),
    }


def ext_net_questions():
    return {
        "cidr": sunbeam.jobs.questions.PromptQuestion(
            "CIDR of network to use for external networking",
            default_value="10.20.20.0/24",
            validation_function=ipaddress.ip_network,
        ),
        "gateway": sunbeam.jobs.questions.PromptQuestion(
            "IP address of default gateway for external network",
            default_value=None,
            validation_function=ipaddress.ip_address,
        ),
        "start": sunbeam.jobs.questions.PromptQuestion(
            "Start of IP allocation range for external network",
            default_value=None,
            validation_function=ipaddress.ip_address,
        ),
        "end": sunbeam.jobs.questions.PromptQuestion(
            "End of IP allocation range for external network",
            default_value=None,
            validation_function=ipaddress.ip_address,
        ),
        "network_type": sunbeam.jobs.questions.PromptQuestion(
            "Network type for access to external network",
            choices=["flat", "vlan"],
            default_value="flat",
        ),
        "segmentation_id": sunbeam.jobs.questions.PromptQuestion(
            "VLAN ID to use for external network", default_value=0
        ),
        "nic": NicQuestion(
            "Free network interface that will be configured for external traffic"
        ),
    }


def ext_net_questions_local_only():
    return {
        "cidr": sunbeam.jobs.questions.PromptQuestion(
            (
                "CIDR of OpenStack external network - arbitrary but must not "
                "be in use"
            ),
            default_value="10.20.20.0/24",
            validation_function=ipaddress.ip_network,
        ),
        "start": sunbeam.jobs.questions.PromptQuestion(
            "Start of IP allocation range for external network",
            default_value=None,
            validation_function=ipaddress.ip_address,
        ),
        "end": sunbeam.jobs.questions.PromptQuestion(
            "End of IP allocation range for external network",
            default_value=None,
            validation_function=ipaddress.ip_address,
        ),
        "network_type": sunbeam.jobs.questions.PromptQuestion(
            "Network type for access to external network",
            choices=["flat", "vlan"],
            default_value="flat",
        ),
        "segmentation_id": sunbeam.jobs.questions.PromptQuestion(
            "VLAN ID to use for external network", default_value=0
        ),
    }


VARIABLE_DEFAULTS = {
    "user": {
        "username": "demo",
        "cidr": "192.168.122.0/24",
        "security_group_rules": True,
    },
    "external_network": {
        "cidr": "10.20.20.0/24",
        "gateway": None,
        "start": None,
        "end": None,
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

    unit = run_sync(jhelper.get_leader_unit(app, model))
    if not unit:
        _message = f"Unable to get {app} leader"
        raise click.ClickException(_message)

    action_result = run_sync(jhelper.run_action(unit, model, action_cmd))
    if action_result.get("return-code", 0) > 1:
        _message = "Unable to retrieve openrc from Keystone service"
        raise click.ClickException(_message)

    return {
        "OS_USERNAME": action_result.get("username"),
        "OS_PASSWORD": action_result.get("password"),
        "OS_AUTH_URL": action_result.get("public-endpoint"),
        "OS_USER_DOMAIN_NAME": action_result.get("user-domain-name"),
        "OS_PROJECT_DOMAIN_NAME": action_result.get("project-domain-name"),
        "OS_PROJECT_NAME": action_result.get("project-name"),
        "OS_AUTH_VERSION": action_result.get("api-version"),
        "OS_IDENTITY_API_VERSION": action_result.get("api-version"),
    }


class SetHypervisorCharmConfigStep(BaseStep):
    """Update openstack-hypervisor charm config"""

    IPVANYNETWORK_UNSET = "0.0.0.0/0"

    def __init__(self, jhelper, ext_network: Path):
        super().__init__(
            "Update charm config",
            "Updating openstack-hypervisor charm configuration",
        )

        # File path with external_network details in json format
        self.ext_network_file = ext_network
        self.ext_network = {}
        self.client = Client()
        self.jhelper = jhelper
        self.charm_config = {}

    def has_prompts(self) -> bool:
        return False

    def run(self, status: Optional["Status"] = None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        self.ext_network = self.variables.get("external_network", {})
        self.charm_config["enable-gateway"] = str(
            self.variables["user"]["remote_access_location"] == utils.REMOTE_ACCESS
        )
        self.charm_config["external-bridge"] = "br-ex"
        if self.variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
            external_network = ipaddress.ip_network(
                self.variables["external_network"].get("cidr")
            )
            bridge_interface = (
                f"{self.ext_network.get('gateway')}/{external_network.prefixlen}"
            )
            self.charm_config["external-bridge-address"] = bridge_interface
        else:
            self.charm_config["external-bridge-address"] = self.IPVANYNETWORK_UNSET

        self.charm_config["physnet-name"] = self.variables["external_network"].get(
            "physical_network"
        )
        try:
            model = CONTROLLER_MODEL.split("/")[-1]
            LOG.debug(
                f"Config to apply on openstack-hypervisor snap: {self.charm_config}"
            )
            run_sync(
                self.jhelper.set_application_config(
                    model,
                    "openstack-hypervisor",
                    self.charm_config,
                )
            )
        except Exception as e:
            LOG.exception("Error setting config for openstack-hypervisor")
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class UserOpenRCStep(BaseStep):
    """Generate openrc for created cloud user."""

    def __init__(self, auth_url: str, auth_version: str, openrc: Path):
        super().__init__(
            "Generate admin openrc", "Generating openrc for cloud admin usage"
        )
        self.auth_url = auth_url
        self.auth_version = auth_version
        self.openrc = openrc
        self.client = Client()

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if "user" not in self.variables:
            LOG.debug("Demo setup not yet done")
            return Result(ResultType.SKIPPED)
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional["Status"] = None) -> Result:
        try:
            snap = Snap()
            terraform = str(snap.paths.snap / "bin" / "terraform")
            cmd = [terraform, "output", "-json"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=snap.paths.user_common / "etc" / "demo-setup",
            )
            # Mask any passwords before printing process.stdout
            tf_output = json.loads(process.stdout)
            self._print_openrc(tf_output)
            return Result(ResultType.COMPLETED)
        except subprocess.CalledProcessError as e:
            LOG.exception("Error initializing Terraform")
            return Result(ResultType.FAILED, str(e))

    def _print_openrc(self, tf_output: dict) -> None:
        """Print openrc to console and save to disk using provided information"""
        _openrc = f"""# openrc for {tf_output["OS_USERNAME"]["value"]}
export OS_AUTH_URL={self.auth_url}
export OS_USERNAME={tf_output["OS_USERNAME"]["value"]}
export OS_PASSWORD={tf_output["OS_PASSWORD"]["value"]}
export OS_USER_DOMAIN_NAME={tf_output["OS_USER_DOMAIN_NAME"]["value"]}
export OS_PROJECT_DOMAIN_NAME={tf_output["OS_PROJECT_DOMAIN_NAME"]["value"]}
export OS_PROJECT_NAME={tf_output["OS_PROJECT_NAME"]["value"]}
export OS_AUTH_VERSION={self.auth_version}
export OS_IDENTITY_API_VERSION={self.auth_version}"""
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
        answer_file: str,
        preseed_file: str = None,
        accept_defaults: bool = False,
    ):
        super().__init__(
            "Collect cloud configuration", "Collecting cloud configuration"
        )
        self.accept_defaults = accept_defaults
        self.preseed_file = preseed_file
        self.client = Client()
        self.answer_file = answer_file

    def has_prompts(self) -> bool:
        return True

    def prompt(self, console: Optional[Console] = None) -> None:
        """Prompt the user for basic cloud configuration.

        Prompts the user for required information for cloud configuration.

        :param console: the console to prompt on
        :type console: rich.console.Console (Optional)
        """
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        for section in ["user", "external_network"]:
            if not self.variables.get(section):
                self.variables[section] = {}
        if self.preseed_file:
            preseed = sunbeam.jobs.questions.read_preseed(Path(self.preseed_file))
        else:
            preseed = {}
        user_bank = sunbeam.jobs.questions.QuestionBank(
            questions=user_questions(),
            console=console,
            preseed=preseed.get("user"),
            previous_answers=self.variables.get("user"),
            accept_defaults=self.accept_defaults,
        )
        self.variables["user"][
            "remote_access_location"
        ] = user_bank.remote_access_location.ask()
        # External Network Configuration
        if self.variables["user"]["remote_access_location"] == utils.LOCAL_ACCESS:
            ext_net_bank = sunbeam.jobs.questions.QuestionBank(
                questions=ext_net_questions_local_only(),
                console=console,
                preseed=preseed.get("external_network"),
                previous_answers=self.variables.get("external_network"),
                accept_defaults=self.accept_defaults,
            )
        else:
            ext_net_bank = sunbeam.jobs.questions.QuestionBank(
                questions=ext_net_questions(),
                console=console,
                preseed=preseed.get("external_network"),
                previous_answers=self.variables.get("external_network"),
                accept_defaults=self.accept_defaults,
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
        self.variables["external_network"]["physical_network"] = VARIABLE_DEFAULTS[
            "external_network"
        ]["physical_network"]
        self.variables["user"]["run_demo_setup"] = user_bank.run_demo_setup.ask()
        if self.variables["user"]["run_demo_setup"]:
            # User configuration
            self.variables["user"]["username"] = user_bank.username.ask()
            self.variables["user"]["password"] = user_bank.password.ask()
            self.variables["user"]["cidr"] = user_bank.cidr.ask()
            self.variables["user"][
                "dns_nameservers"
            ] = user_bank.nameservers.ask().split()
            self.variables["user"][
                "security_group_rules"
            ] = user_bank.security_group_rules.ask()
            default_allocation_range_start = self.variables["external_network"].get(
                "start"
            ) or str(external_network_hosts[1])
            self.variables["external_network"]["start"] = ext_net_bank.start.ask(
                new_default=default_allocation_range_start
            )
            default_allocation_range_end = self.variables["external_network"].get(
                "end"
            ) or str(external_network_hosts[-1])
            self.variables["external_network"]["end"] = ext_net_bank.end.ask(
                new_default=default_allocation_range_end
            )

            self.variables["external_network"][
                "network_type"
            ] = ext_net_bank.network_type.ask()
            if self.variables["external_network"]["network_type"] == "vlan":
                self.variables["external_network"][
                    "segmentation_id"
                ] = ext_net_bank.segmentation_id.ask()
            else:
                self.variables["external_network"]["segmentation_id"] = 0

        sunbeam.jobs.questions.write_answers(
            self.client, CLOUD_CONFIG_SECTION, self.variables
        )

    def run(self, status: Optional[Status] = None) -> Result:
        return Result(ResultType.COMPLETED)


class DemoSetup(BaseStep):
    """Default cloud configuration for all-in-one install."""

    def __init__(
        self,
        tfhelper: TerraformHelper,
        answer_file: str,
    ):
        super().__init__(
            "Create demonstration configuration",
            "Creating demonstration user, project and networking",
        )
        self.answer_file = answer_file
        self.tfhelper = tfhelper
        self.client = Client()

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        self.variables = sunbeam.jobs.questions.load_answers(
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
        tfhelper: TerraformHelper,
    ):
        super().__init__(tfhelper)
        self.tfhelper = tfhelper
        self.client = Client()

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        if self.variables["user"]["run_demo_setup"]:
            return Result(ResultType.COMPLETED)
        else:
            return Result(ResultType.SKIPPED)


class SetLocalHypervisorOptions(BaseStep):
    def __init__(
        self, name, jhelper, join_mode: bool = False, preseed_file: Path = None
    ):
        super().__init__(
            "Apply local hypervisor settings", "Applying local hypervisor settings"
        )
        self.name = name
        self.jhelper = jhelper
        self.join_mode = join_mode
        self.preseed_file = preseed_file
        self.client = Client()
        self.preseed_file = preseed_file

    def has_prompts(self) -> bool:
        return True

    def prompt_for_nic(self) -> None:
        """Prompt user for nic to use and do some validation."""
        ext_net_bank = sunbeam.jobs.questions.QuestionBank(
            questions=ext_net_questions(),
            console=console,
            accept_defaults=False,
        )
        nic = None
        while True:
            nic = ext_net_bank.nic.ask()
            if utils.is_configured(nic):
                agree_nic_up = sunbeam.jobs.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is configured. Any "
                    "configuration will be lost, are you sure you want to "
                    "continue?"
                ).ask()
                if not agree_nic_up:
                    continue
            if utils.is_nic_up(nic) and not utils.is_nic_connected(nic):
                agree_nic_no_link = sunbeam.jobs.questions.ConfirmQuestion(
                    f"WARNING: Interface {nic} is not connected. Are "
                    "you sure you want to continue?"
                ).ask()
                if not agree_nic_no_link:
                    continue
            break
        return nic

    def prompt(self, console: Optional[Console] = None) -> None:
        self.nic = None
        # If adding a node before configure step has run then answers will
        # not be populated yet.
        self.variables = sunbeam.jobs.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        remote_access_location = self.variables.get("user", {}).get(
            "remote_access_location"
        )
        if self.preseed_file:
            preseed = sunbeam.jobs.questions.read_preseed(self.preseed_file)
        else:
            preseed = {}
        # If adding new nodes to the cluster then local access makes no sense
        # so always prompt for the nic.
        if self.join_mode or remote_access_location == utils.REMOTE_ACCESS:
            ext_net_preseed = preseed.get("external_network", {})
            # If nic is in the preseed assume the user knows what they are doing and
            # bypass validation
            if ext_net_preseed.get("nic"):
                self.nic = ext_net_preseed.get("nic")
            else:
                self.nic = self.prompt_for_nic()

    def run(self, status: Optional[Status] = None) -> Result:
        if not self.nic:
            return Result(ResultType.COMPLETED)
        node = self.client.cluster.get_node_info(self.name)
        self.machine_id = str(node.get("machineid"))
        app = "openstack-hypervisor"
        action_cmd = "set-hypervisor-local-settings"
        model = CONTROLLER_MODEL.split("/")[-1]
        unit = run_sync(self.jhelper.get_unit_from_machine(app, self.machine_id, model))
        action_result = run_sync(
            self.jhelper.run_action(
                unit.entity_id,
                model,
                action_cmd,
                action_params={
                    "external-nic": self.nic,
                },
            )
        )

        if action_result.get("return-code", 0) > 1:
            _message = "Unable to set local hypervisor configuration"
            return Result(ResultType.FAILED, _message)
        return Result(ResultType.COMPLETED)


@click.command()
@click.option("-a", "--accept-defaults", help="Accept all defaults.", is_flag=True)
@click.option(
    "-p",
    "--preseed",
    help="Preseed file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--openrc",
    help="Output file for cloud access details.",
    type=click.Path(dir_okay=False, path_type=Path),
)
def configure(
    openrc: Optional[Path] = None,
    preseed: Optional[Path] = None,
    accept_defaults: bool = False,
) -> None:
    """Configure cloud with some sensible defaults."""
    preflight_checks = []
    preflight_checks.append(DaemonGroupCheck())
    preflight_checks.append(VerifyBootstrappedCheck())
    run_preflight_checks(preflight_checks, console)

    name = utils.get_fqdn()
    snap = Snap()
    src = snap.paths.snap / "etc" / "demo-setup/"
    dst = snap.paths.user_common / "etc" / "demo-setup"
    try:
        os.mkdir(dst)
    except FileExistsError:
        pass
    # NOTE: install to user writable location
    LOG.debug(f"Updating {dst} from {src}...")
    shutil.copytree(src, dst, dirs_exist_ok=True)

    data_location = snap.paths.user_data
    jhelper = JujuHelper(data_location)
    try:
        run_sync(jhelper.get_model(OPENSTACK_MODEL))
    except ModelNotFoundException:
        LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first")
    admin_credentials = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
    tfhelper = TerraformHelper(
        path=snap.paths.user_common / "etc" / "demo-setup",
        env=admin_credentials,
        plan="demo-setup",
        backend="http",
        data_location=data_location,
    )
    answer_file = tfhelper.path / "config.auto.tfvars.json"
    plan = [
        JujuLoginStep(data_location),
        UserQuestions(
            answer_file=answer_file,
            preseed_file=preseed,
            accept_defaults=accept_defaults,
        ),
        TerraformDemoInitStep(tfhelper),
        DemoSetup(
            tfhelper=tfhelper,
            answer_file=answer_file,
        ),
        UserOpenRCStep(
            auth_url=admin_credentials["OS_AUTH_URL"],
            auth_version=admin_credentials["OS_AUTH_VERSION"],
            openrc=openrc,
        ),
        SetHypervisorCharmConfigStep(jhelper, ext_network=answer_file),
        SetLocalHypervisorOptions(
            name,
            jhelper,
            # Accept preseed file but do not allow 'accept_defaults' as nic
            # selection may vary from machine to machine and is potentially
            # destructive if it takes over an unintended nic.
            preseed_file=preseed,
        ),
    ]
    run_plan(plan, console)
