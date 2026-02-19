# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import datetime
import logging
import os
import sys

import click
import yaml
from rich.console import Console
from rich.table import Table
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.common import (
    FORMAT_TABLE,
    FORMAT_YAML,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.errors import SunbeamException

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
    all_plans = set(plans).union(locks)
    if format == FORMAT_TABLE:
        table = Table()
        table.add_column("Plan", justify="left")
        table.add_column("Locked", justify="center")
        for plan in all_plans:
            table.add_row(
                plan,
                "x" if plan in locks else "",
            )
        console.print(table)
    elif format == FORMAT_YAML:
        plan_states = {
            plan: "locked" if plan in locks else "unlocked" for plan in all_plans
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


@plans.command("shell")
@click.argument("plan", type=str, required=False)
@click.pass_context
def shell_plan(ctx: click.Context, plan: str | None = None):
    """Allows debugging terraform plans.

    Debugging tool dropping the user into a shell
    with the environment variables set for the specified plan.
    """
    deployment: Deployment = ctx.obj
    jhelper_keystone = deployment.get_juju_helper(keystone=True)

    with console.status("Fetching OS admin credentials..."):
        if deployment.get_client().cluster.check_sunbeam_bootstrapped():
            try:
                admin_credentials = retrieve_admin_credentials(
                    jhelper_keystone, deployment, OPENSTACK_MODEL
                )
                os.environ.update(admin_credentials)
            except SunbeamException:
                LOG.warning(
                    "Failed to retrieve admin credentials, proceeding without them."
                )

    with console.status("Fetching terraform plan information..."):
        effective_plan = plan or "sunbeam-machine-plan"
        try:
            tfhelper = deployment.get_tfhelper(effective_plan)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        if env := tfhelper.env:
            os.environ.update(env)
    # drop in tf plans root if no plan selected
    pwd = tfhelper.path if plan else tfhelper.path.parent
    os.chdir(pwd)
    console.print("You can now run `terraform` commands in this shell.")
    console.print("Type `exit` to return to the normal shell.")
    sys.exit(os.system("bash"))
