# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console

from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.common import run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.steps.juju import JujuLoginStep

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click.pass_context
def openrc(ctx: click.Context) -> None:
    """Retrieve openrc for cloud admin account."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = []
    preflight_checks.append(VerifyBootstrappedCheck(client))
    run_preflight_checks(preflight_checks, console)

    # Login to the Juju controller
    run_plan([JujuLoginStep(deployment.juju_account)], console)

    jhelper = deployment.get_juju_helper(keystone=True)

    with console.status("Retrieving openrc from Keystone service ... "):
        creds = retrieve_admin_credentials(
            jhelper,
            deployment,
            OPENSTACK_MODEL,
        )
        console.print("# openrc for access to OpenStack")
        for param, value in creds.items():
            console.print(f"export {param}={value}")
