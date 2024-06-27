# Copyright (c) 2024 Canonical Ltd.
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

import logging

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.commands.juju import RegisterRemoteJujuUserStep, SwitchToController
from sunbeam.jobs.checks import VerifyBootstrappedCheck
from sunbeam.jobs.common import run_plan, run_preflight_checks
from sunbeam.jobs.deployment import Deployment

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Replace if private controller exists with same name",
)
@click.option(
    "-t",
    "--token",
    type=str,
    required=True,
    help="Registration token to login to private controller",
)
@click.option(
    "-u",
    "--user",
    type=str,
    required=True,
    help="User name to login to private controller",
)
@click.option(
    "-n", "--name", type=str, required=True, help="Name of the private controller"
)
@click.pass_context
def register_controller(
    ctx: click.Context, name: str, user: str, token: str, force: bool
) -> None:
    """Register private controller."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    data_location = Snap().paths.user_data

    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)

    plan = [
        RegisterRemoteJujuUserStep(
            client, user, token, name, data_location, replace=force
        ),
        SwitchToController(deployment.controller),
    ]

    run_plan(plan, console)
    console.print(f"Private controller {name} registered")
