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

import datetime
import logging

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
)
from sunbeam.core.deployment import Deployment

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.group()
def plans():
    """Manage terraform plans."""
    pass


@plans.command("list")
@click.option(
    "-f",
    "--format",
    type=click.Choice([FORMAT_TABLE, FORMAT_YAML]),
    default=FORMAT_TABLE,
    help="Output format.",
)
@click.pass_context
def list_plans(ctx: click.Context, format: str):
    """List terraform plans and their lock status."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    plans = client.cluster.list_terraform_plans()
    locks = client.cluster.list_terraform_locks()
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Plan", justify="left")
        table.add_column("Locked", justify="center")
        for plan in plans:
            table.add_row(
                plan,
                "x" if plan in locks else "",
            )
        console.print(table)
    elif format == FORMAT_YAML:
        plan_states = {
            plan: "locked" if plan in locks else "unlocked" for plan in plans
        }
        console.print(yaml.dump(plan_states))


@plans.command("unlock")
@click.argument("plan", type=str)
@click.option("--force", is_flag=True, default=False, help="Force unlock the plan.")
@click.pass_context
def unlock_plan(ctx: click.Context, plan: str, force: bool):
    """Unlock a terraform plan."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    try:
        lock = client.cluster.get_terraform_lock(plan)
    except ConfigItemNotFoundException as e:
        raise click.ClickException(f"Lock for {plan!r} not found") from e
    if not force:
        lock_creation_time = datetime.datetime.strptime(
            lock["Created"][:-4] + "Z", "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        if datetime.datetime.utcnow() - lock_creation_time < datetime.timedelta(
            hours=1
        ):
            click.confirm(
                f"Plan {plan!r} was locked less than an hour ago,"
                " are you sure you want to unlock it?",
                abort=True,
            )
    try:
        client.cluster.unlock_terraform_plan(plan, lock)
    except ConfigItemNotFoundException as e:
        raise click.ClickException(f"Lock for {plan!r} not found") from e
    console.print(f"Unlocked plan {plan!r}")
