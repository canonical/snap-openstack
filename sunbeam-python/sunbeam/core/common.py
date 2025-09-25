# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import enum
import ipaddress
import json
import logging
import os
import queue
import threading
import typing
from pathlib import Path
from typing import Any, Sequence, Type, TypeVar

import click
import yaml
from click import decorators
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap, UnknownConfigKey
from tenacity import RetryCallState

from sunbeam.clusterd.client import Client

LOG = logging.getLogger(__name__)
RAM_16_GB_IN_KB = 16 * 1000 * 1000
RAM_32_GB_IN_KB = 32 * 1000 * 1000
RAM_32_GB_IN_MB = 32 * 1000
RAM_4_GB_IN_MB = 4 * 1000

# Formatting related constants
FORMAT_JSON = "json"
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

AnyAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class Role(enum.Enum):
    """The role that the current node will play.

    This determines if the role will be a control plane node, a Compute node,
    or a storage node. The role will help determine which particular services
    need to be configured and installed on the system.
    """

    CONTROL = 1
    COMPUTE = 2
    STORAGE = 3
    NETWORK = 4

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

    def is_network_node(self) -> bool:
        """Returns True if the node requires network services.

        Network services are installed on nodes which are designated
        for network nodes only. This helps determine the role that the local
        node will play.

        :return: True if the node should have network services,
                 False otherwise
        """
        return self == Role.NETWORK


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
                    f"{key} was specified but already exists on this StepResult."
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


def run_plan(
    plan: Sequence[BaseStep],
    console: Console,
    no_hint: bool = True,
    no_raise: bool = False,
) -> dict:
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
                step.prompt(console, no_hint)
                status.start()

            skip_result = step.is_skip(status)
            if skip_result.result_type == ResultType.SKIPPED:
                results[step.__class__.__name__] = skip_result
                LOG.debug(f"Skipping step {step.name}")
                continue

            if skip_result.result_type == ResultType.FAILED:
                if no_raise:
                    results[step.__class__.__name__] = skip_result
                    break
                raise click.ClickException(skip_result.message)

            LOG.debug(f"Running step {step.name}")
            result = step.run(status)
            results[step.__class__.__name__] = result
            LOG.debug(
                f"Finished running step {step.name!r}. Result: {result.result_type}"
            )

        if result.result_type == ResultType.FAILED:
            if no_raise:
                break
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


def click_option_database(func: click.decorators.FC) -> click.decorators.FC:
    return click.option(
        "--database",
        default="auto",
        type=click.Choice(
            [
                "auto",
                "single",
                "multi",
            ],
            case_sensitive=False,
        ),
        help=(
            "This option is deprecated and the value is ignored. "
            "Instead user is prompted to select the database topology. "
            "The database topology can also be set via manifest."
        ),
    )(func)


def update_config(client: Client, key: str, config: dict):
    client.cluster.update_config(key, json.dumps(config))


def read_config(client: Client, key: str) -> dict:
    config = client.cluster.get_config(key)
    return json.loads(config)


def delete_config(client: Client, key: str):
    client.cluster.delete_config(key)


STATUS_READY = "ready"
STATUS_NOT_READY = "not_ready"


class _UpdateStatusThread(threading.Thread):
    """Thread to update status in the background."""

    def __init__(
        self,
        step,
        applications: list[str],
        queue: queue.Queue,
        status: Status | None = None,
    ):
        super().__init__(target=self.run, name="UpdateStatusThread", daemon=True)
        self._stop_event = threading.Event()
        self.step = step
        self.applications = applications
        self.queue = queue
        self.status = status

    def stop(self):
        """Stop the thread."""
        LOG.debug("Stopping background status update thread")
        self._stop_event.set()

    def stopped(self) -> bool:
        """Check if the thread is stopped."""
        return self._stop_event.is_set()

    def _nb_active_apps(self, apps: dict[str, int]) -> int:
        """Only count apps with count 4+."""
        return sum(1 for count in apps.values() if count >= 4)

    def run(self):
        """Run the thread."""
        if self.status is None:
            LOG.debug("No status provided, skipping background status update")
            return
        LOG.debug("Starting background status update thread")
        apps = dict.fromkeys(self.applications, 0)
        nb_apps = len(self.applications)
        message = (
            self.step.status
            + "waiting for services to come online ({nb_active_apps}/{nb_apps})"
        )
        nb_active_apps = 0
        self.status.update(
            message.format(nb_active_apps=nb_active_apps, nb_apps=nb_apps)
        )
        while nb_active_apps < nb_apps:
            if self.stopped():
                LOG.debug(
                    "Cancelling status update, not ready applications: %s",
                    ", ".join(app for app, ready in apps.items() if not ready),
                )
                return
            try:
                status, app = self.queue.get(timeout=15)
            except queue.Empty:
                continue
            if app not in apps:
                LOG.debug("Received an unexpected app %s", app)
                self.queue.task_done()
                continue
            if status == STATUS_READY:
                apps[app] += 1
            else:
                apps[app] = 0
            nb_active_apps = self._nb_active_apps(apps)
            self.status.update(
                message.format(nb_active_apps=nb_active_apps, nb_apps=nb_apps)
            )
            self.queue.task_done()
        self.status.update(self.step.status + "all services are online")


def update_status_background(
    step,
    applications: list[str],
    queue: queue.Queue,
    status: Status | None = None,
) -> _UpdateStatusThread:
    """Update status in the background.

    If status is None, return a no-op task.
    """
    updater = _UpdateStatusThread(step, applications, queue, status)
    updater.start()
    return updater


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


def infer_version(snap: Snap) -> str:
    """Compute version from environment."""
    try:
        version = str(snap.config.get("deployment.version"))
    except UnknownConfigKey:
        return "2024.1"
    return version


def parse_ip_range(
    ip_range: str, separator: str = "-"
) -> (
    tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]
    | tuple[ipaddress.IPv6Address, ipaddress.IPv6Address]
):
    """Parse an IP range in the form of 'ip-ip' into a tuple of addresses."""
    ips = ip_range.split("-")
    if len(ips) != 2:
        raise ValueError("Invalid IP range, must be in the form of 'ip-ip'")
    ip1 = ipaddress.ip_address(ips[0].strip())
    ip2 = ipaddress.ip_address(ips[1].strip())
    if not isinstance(ip1, type(ip2)):
        raise ValueError("IP addresses must be of the same type (IPv4 or IPv6)")
    if isinstance(ip1, ipaddress.IPv4Address):
        return (ip1, typing.cast(ipaddress.IPv4Address, ip2))
    return (ip1, typing.cast(ipaddress.IPv6Address, ip2))


def parse_ip_range_or_cidr(
    ip_range: str, separator: str = "-"
) -> (
    tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]
    | tuple[ipaddress.IPv6Address, ipaddress.IPv6Address]
    | ipaddress.IPv4Network
    | ipaddress.IPv6Network
):
    """Parse an IP range or CIDR notation into a tuple of addresses or networks."""
    ips = ip_range.split(separator)
    if len(ips) == 1:
        if "/" not in ips[0]:
            raise ValueError("Invalid CIDR definition, must be in the form 'ip/mask'")
        return ipaddress.ip_network(ips[0].strip())
    elif len(ips) == 2:
        return parse_ip_range(ip_range, separator)
    else:
        raise ValueError("Invalid IP range, must be in the form of 'ip-ip' or 'cidr'")


def validate_cidr_or_ip_ranges(ip_ranges: str):
    for ip_range in ip_ranges.split(","):
        validate_cidr_or_ip_range(ip_range)


def validate_cidr_or_ip_range(ip_range: str):
    _ = parse_ip_range_or_cidr(ip_range, separator="-")


def validate_ip_range(ip_range: str):
    ips = parse_ip_range_or_cidr(ip_range, separator="-")
    if not isinstance(ips, tuple):
        raise ValueError("Invalid IP range, must be in the form of 'ip-ip'")


def convert_retry_failure_as_result(retry_state: RetryCallState) -> Result:
    if retry_state.outcome is not None:
        return Result(ResultType.FAILED, str(retry_state.outcome.exception()))
    else:
        return Result(ResultType.FAILED)
