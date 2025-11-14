# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console
from snaphelpers import Snap

from sunbeam.core.checks import VerifyBootstrappedCheck, run_preflight_checks
from sunbeam.core.common import (
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.steps.juju import JujuLoginStep
from sunbeam.utils import click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()
snap = Snap()


@click.command()
@click_option_show_hints
@click.pass_context
def juju_login(ctx: click.Context, show_hints: bool) -> None:
    """Login to the controller with current host user."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    preflight_checks = [VerifyBootstrappedCheck(client)]
    run_preflight_checks(preflight_checks, console)

    plan = [JujuLoginStep(deployment.juju_account)]
    if deployment.region_ctrl_juju_controller:
        plan.append(
            JujuLoginStep(
                deployment.region_ctrl_juju_account,
                deployment.region_ctrl_juju_controller.name,
            ),
        )

    run_plan(plan, console, show_hints)

    console.print("Juju re-login complete.")
