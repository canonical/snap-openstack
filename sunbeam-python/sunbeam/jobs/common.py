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

import enum
import json
import logging
import os
from typing import List, Optional, Type

import click
from click import decorators
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client

LOG = logging.getLogger(__name__)
RAM_16_GB_IN_KB = 16 * 1024 * 1024
RAM_32_GB_IN_KB = 32 * 1024 * 1024


class Role(enum.Enum):
    """The role that the current node will play

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


def roles_to_str_list(roles: List[Role]) -> List[str]:
    return [role.name.lower() for role in roles]


class ResultType(enum.Enum):
    COMPLETED = 0
    FAILED = 1
    SKIPPED = 2


class Result:
    """The result of running a step"""

    def __init__(self, result_type: ResultType, message: Optional[str] = ""):
        """Creates a new result

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
        """Initialise the BaseStep

        :param name: the name of the step
        """
        self.name = name
        self.description = description

    def prompt(self, console: Optional[Console] = None) -> None:
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

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                 ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status]) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate

        :return:
        """
        pass

    @property
    def status(self):
        """Returns the status to display.

        :return: the status of the step
        """
        return self.description + " ... "


def run_preflight_checks(checks: list, console: Console):
    """Run preflight checks sequentially.

    Runs each checks, logs whether the check passed or failed.
    Exits at first failure.

    Raise ClickException in case of Result Failures.
    """
    for check in checks:
        LOG.debug(f"Starting pre-flight check {check.name}")
        message = f"{check.description} ... "
        with console.status(message):
            if not check.run():
                raise click.ClickException(check.message)


def run_plan(plan: List[BaseStep], console: Console) -> dict:
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


def get_step_message(plan_results: dict, step: Type[BaseStep]) -> Optional[str]:
    """Utility to get a step result's message."""
    result = plan_results.get(step.__name__)
    if result:
        return result.message
    return None


def validate_roles(
    ctx: click.core.Context, param: click.core.Option, value: tuple
) -> List[Role]:
    try:
        return [Role[role.upper()] for role in value]
    except KeyError as e:
        raise click.BadParameter(str(e))


def get_host_total_ram() -> int:
    """Reads meminfo to get total ram in KB."""
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal"):
                return int(line.split()[1])
    raise Exception("Could not determine total RAM")


def get_host_total_cores() -> int:
    """Return total cpu count."""
    return os.cpu_count()


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
