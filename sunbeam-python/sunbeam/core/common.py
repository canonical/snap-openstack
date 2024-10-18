# Copyright (c) 2023 Canonical Ltd.
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

import asyncio
import enum
import ipaddress
import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence, Type, TypeVar

import click
import yaml
from click import decorators
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap, UnknownConfigKey

from sunbeam.clusterd.client import Client

LOG = logging.getLogger(__name__)
RAM_16_GB_IN_KB = 16 * 1000 * 1000
RAM_32_GB_IN_KB = 32 * 1000 * 1000
RAM_32_GB_IN_MB = 32 * 1000
RAM_4_GB_IN_MB = 4 * 1000

# Formatting related constants
FORMAT_TABLE = "table"
FORMAT_YAML = "yaml"
FORMAT_DEFAULT = "default"
FORMAT_VALUE = "value"

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
SHARE_PATH = Path(".local/share/openstack/")

CLICK_OK = "[green]OK[/green]"
CLICK_FAIL = "[red]FAIL[/red]"
CLICK_WARN = "[yellow]WARN[/yellow]"

DEFAULT_JUJU_NO_PROXY_SETTINGS = "127.0.0.1,localhost,::1"
K8S_CLUSTER_SERVICE_CIDR = "10.152.183.0/24"
K8S_CLUSTER_POD_CIDR = "10.1.0.0/16"

BaseStepSubclass = TypeVar("BaseStepSubclass", bound="BaseStep")


class Role(enum.Enum):
    """The role that the current node will play.

    This determines if the role will be a control plane node, a Compute node,
    or a storage node. The role will help determine which particular services
    need to be configured and installed on the system.
    """

    CONTROL = 1
    COMPUTE = 2
    STORAGE = 3

    def is_control_node(self) -> bool:
        """Returns True if the node requires control services.

        Control plane services are installed on nodes which are not designated
        for compute nodes only. This helps determine the role that the local
        node will play.

        :return: True if the node should have control-plane services,
                 False otherwise
        """
        return self == Role.CONTROL

    def is_compute_node(self) -> bool:
        """Returns True if the node requires compute services.

        Compute services are installed on nodes which are not designated as
        control nodes only. This helps determine the services which are
        necessary to install.

        :return: True if the node should run Compute services,
                 False otherwise
        """
        return self == Role.COMPUTE

    def is_storage_node(self) -> bool:
        """Returns True if the node requires storage services.

        Storage services are installed on nodes which are designated
        for storage nodes only. This helps determine the role that the local
        node will play.

        :return: True if the node should have storage services,
                 False otherwise
        """
        return self == Role.STORAGE


def roles_to_str_list(roles: list[Role]) -> list[str]:
    return [role.name.lower() for role in roles]


class ResultType(enum.Enum):
    COMPLETED = 0
    FAILED = 1
    SKIPPED = 2


class Result:
    """The result of running a step."""

    def __init__(self, result_type: ResultType, message: Any = ""):
        """Creates a new result.

        :param result_type:
        :param message:
        """
        self.result_type = result_type
        self.message = message


class StepResult:
    """The Result of running a Step.

    The results of running contain the minimum of the ResultType to indicate
    whether running the Step was completed, failed, or skipped.
    """

    def __init__(self, result_type: ResultType = ResultType.COMPLETED, **kwargs):
        """Creates a new StepResult.

        The StepResult will contain various information regarding the result
        of running a Step. By default, a new StepResult will be created with
        result_type set to ResultType.COMPLETED.

        Additional attributes can be stored in the StepResult object by using
        the kwargs values, but the keys must be unique to the StepResult
        already. If the kwargs contains a keyword that is an attribute on the
        object then a ValueError is raised.

        :param result_type: the result of running a plan or step.
        :param kwargs: additional attributes to store in the step.
        :raises: ValueError if a key in the kwargs already exists on the
                 object.
        """
        self.result_type = result_type
        for key, value in kwargs.items():
            # Note(wolsen) this is a bit of a defensive check to make sure
            # a bit of code doesn't accidentally override a base object
            # attribute.
            if hasattr(self, key):
                raise ValueError(
                    f"{key} was specified but already exists on " f"this StepResult."
                )
            self.__setattr__(key, value)


class BaseStep:
    """A step defines a logical unit of work to be done as part of a plan.

    A step determines what needs to be done in order to perform a logical
    action as part of carrying out a plan.
    """

    def __init__(self, name: str, description: str = ""):
        """Initialise the BaseStep.

        :param name: the name of the step
        """
        self.name = name
        self.description = description

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        pass

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        return Result(ResultType.COMPLETED)

    @property
    def status(self):
        """Returns the status to display.

        :return: the status of the step
        """
        return self.description + " ... "

    def update_status(self, status: Status | None, msg: str):
        """Update status if status is provided."""
        if status is not None:
            status.update(self.status + msg)


def run_plan(plan: Sequence[BaseStep], console: Console) -> dict:
    """Run plans sequentially.

    Runs each step of the plan, logs each step of
    the plan and returns a dictionary of results
    from each step.

    Raise ClickException in case of Result Failures.
    """
    results = {}

    for step in plan:
        LOG.debug(f"Starting step {step.name!r}")
        with console.status(step.status) as status:
            if step.has_prompts():
                status.stop()
                step.prompt(console)
                status.start()

            skip_result = step.is_skip(status)
            if skip_result.result_type == ResultType.SKIPPED:
                results[step.__class__.__name__] = skip_result
                LOG.debug(f"Skipping step {step.name}")
                continue

            if skip_result.result_type == ResultType.FAILED:
                raise click.ClickException(skip_result.message)

            LOG.debug(f"Running step {step.name}")
            result = step.run(status)
            results[step.__class__.__name__] = result
            LOG.debug(
                f"Finished running step {step.name!r}. Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            raise click.ClickException(result.message)

    # Returns results object only when all steps have results of type
    # COMPLETED or SKIPPED.
    return results


def get_step_result(plan_results: dict, step: Type[BaseStepSubclass]) -> Result:
    """Utility to get a step result."""
    return plan_results[step.__name__]


def get_step_message(plan_results: dict, step: Type[BaseStep]) -> Any:
    """Utility to get a step result's message."""
    result = plan_results.get(step.__name__)
    if result:
        return result.message
    return None


def validate_roles(
    ctx: click.core.Context, param: click.core.Option, value: Sequence[str]
) -> list[Role]:
    """Validate roles."""
    roles: set[str] = set()
    for val in value:
        roles.update(val.split(","))
    try:
        return [Role[role.upper()] for role in roles]
    except KeyError as e:
        raise click.BadParameter(
            f"{str(e)}. Valid choices are "
            + ", ".join(role.lower() for role in Role.__members__)
        ) from e


def get_host_total_ram() -> int:
    """Reads meminfo to get total ram in KB."""
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal"):
                return int(line.split()[1])
    raise Exception("Could not determine total RAM")


def get_host_total_cores() -> int:
    """Return total cpu count."""
    return os.cpu_count() or 1


def click_option_topology(func: decorators.FC) -> decorators.FC:
    return click.option(
        "--topology",
        default="auto",
        type=click.Choice(
            [
                "auto",
                "single",
                "multi",
                "large",
            ],
            case_sensitive=False,
        ),
        help=(
            "Allows definition of the intended cluster configuration: "
            "'auto' for automatic determination, "
            "'single' for a single-node cluster, "
            "'multi' for a multi-node cluster, "
            "'large' for a large scale cluster"
        ),
    )(func)


def update_config(client: Client, key: str, config: dict):
    client.cluster.update_config(key, json.dumps(config))


def read_config(client: Client, key: str) -> dict:
    config = client.cluster.get_config(key)
    return json.loads(config)


def delete_config(client: Client, key: str):
    client.cluster.delete_config(key)


async def update_status_background(
    step,
    applications: list[str],
    queue: asyncio.queues.Queue,
    status: Status | None = None,
) -> asyncio.Task:
    """Update status in the background.

    If status is None, return a no-op task.
    """
    if status is None:
        return asyncio.create_task(asyncio.sleep(0))
    apps = dict.fromkeys(applications, False)
    nb_apps = len(applications)
    message = (
        step.status + "waiting for services to come online ({nb_active_apps}/{nb_apps})"
    )

    async def _update_status_background_coro():
        nb_active_apps = 0
        status.update(message.format(nb_active_apps=nb_active_apps, nb_apps=nb_apps))
        while nb_active_apps < nb_apps:
            try:
                app = await queue.get()
                if app not in apps:
                    LOG.debug("Received an unexpected app %s", app)
                    queue.task_done()
                    continue
                apps[app] = True
                nb_active_apps = sum(apps.values())
                status.update(
                    message.format(nb_active_apps=nb_active_apps, nb_apps=nb_apps)
                )
                queue.task_done()
            except asyncio.CancelledError:
                LOG.debug(
                    "Cancelling status update, not ready applications: %s",
                    ", ".join(app for app, ready in apps.items() if not ready),
                )
                break
        status.update(step.status + "all services are online")

    return asyncio.create_task(_update_status_background_coro())


def str_presenter(dumper: yaml.Dumper | yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Return multiline string as '|' literal block.

    Ref: https://stackoverflow.com/questions/8640959/how-can-i-control-what-scalar-form-pyyaml-uses-for-my-data
    """  # noqa W505
    if data.count("\n") > 0:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _get_default_no_proxy_settings() -> set:
    """Return default no proxy settings."""
    return {
        "127.0.0.1",
        "localhost",
        K8S_CLUSTER_SERVICE_CIDR,
        K8S_CLUSTER_POD_CIDR,
        ".svc",
        ".svc.cluster.local",
    }


def convert_proxy_to_model_configs(proxy_settings: dict) -> dict:
    """Convert proxies to juju model configs."""
    return {
        "juju-http-proxy": proxy_settings.get("HTTP_PROXY", ""),
        "juju-https-proxy": proxy_settings.get("HTTPS_PROXY", ""),
        "juju-no-proxy": proxy_settings.get("NO_PROXY", DEFAULT_JUJU_NO_PROXY_SETTINGS),
        "snap-http-proxy": proxy_settings.get("HTTP_PROXY", ""),
        "snap-https-proxy": proxy_settings.get("HTTPS_PROXY", ""),
    }


class SunbeamException(Exception):
    """Base exception for sunbeam."""

    pass


class RiskLevel(str, enum.Enum):
    STABLE = "stable"
    CANDIDATE = "candidate"
    BETA = "beta"
    EDGE = "edge"

    __ordering__ = (STABLE, CANDIDATE, BETA, EDGE)

    def __str__(self) -> str:
        """Return the string representation of the risk level."""
        return self.value

    def __eq__(self, value: object) -> bool:
        """Implement equality comparison."""
        return str(self) == str(value)

    def __lt__(self, other: str) -> bool:
        """Implement less than comparison."""
        if self == other:
            return False
        str_self = str(self)
        str_other = str(other)
        for elem in self.__ordering__:
            if str_self == elem:
                return True
            elif str_other == elem:
                return False
        return False

    def __le__(self, other: str) -> bool:
        """Implement less than or equal comparison."""
        return self < other or self == other

    def __gt__(self, other: str) -> bool:
        """Implement greater than comparison."""
        return not self < other and self != other

    def __ge__(self, other: str) -> bool:
        """Implement greater than or equal comparison."""
        return not self < other or self == other


def infer_risk(snap: Snap) -> RiskLevel:
    """Compute risk level from environment."""
    try:
        risk = snap.config.get("deployment.risk")
    except UnknownConfigKey:
        return RiskLevel.STABLE

    match risk:
        case "candidate":
            return RiskLevel.CANDIDATE
        # Beta and edge are considered the same for now
        case "beta":
            return RiskLevel.BETA
        case "edge":
            return RiskLevel.EDGE
        case _:
            return RiskLevel.STABLE


def validate_cidr_or_ip_ranges(ip_ranges: str):
    for ip_range in ip_ranges.split(","):
        validate_cidr_or_ip_range(ip_range)


def validate_cidr_or_ip_range(ip_range: str):
    ips = ip_range.split("-")
    if len(ips) == 1:
        if "/" not in ips[0]:
            raise ValueError("Invalid CIDR definition, must be in the form 'ip/mask'")
        ipaddress.ip_network(ips[0])
    elif len(ips) == 2:
        ipaddress.ip_address(ips[0])
        ipaddress.ip_address(ips[1])
    else:
        raise ValueError("Invalid IP range, must be in the form of 'ip-ip' or 'cidr'")


def validate_ip_range(ip_range: str):
    ips = ip_range.split("-")
    if len(ips) == 2:
        ipaddress.ip_address(ips[0])
        ipaddress.ip_address(ips[1])
    else:
        raise ValueError("Invalid IP range, must be in the form of 'ip-ip'")
