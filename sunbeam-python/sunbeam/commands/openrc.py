# SPDX-FileCopyrightText: 2022 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console

from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core import juju
from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL

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

    jhelper = juju.JujuHelper(deployment.juju_controller)

    with console.status("Retrieving openrc from Keystone service ... "):
        creds = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)
        console.print("# openrc for access to OpenStack")
        for param, value in creds.items():
            console.print(f"export {param}={value}")
