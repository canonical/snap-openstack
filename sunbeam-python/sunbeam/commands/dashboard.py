# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console

from sunbeam.core import juju
from sunbeam.core.checks import (
    JujuLoginCheck,
    VerifyBootstrappedCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    PromptMode,
    run_plan,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import write_answers
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.steps.horizon import THEME_CONFIG_SECTION, AttachHorizonThemeStep
from sunbeam.utils import click_option_show_hints

LOG = logging.getLogger(__name__)
console = Console()


def retrieve_dashboard_url(jhelper: juju.JujuHelper) -> str:
    """Retrieve dashboard URL from Horizon service."""
    model = OPENSTACK_MODEL
    app = "horizon"
    action_cmd = "get-dashboard-url"
    try:
        unit = jhelper.get_leader_unit(app, model)
    except juju.LeaderNotFoundException:
        raise ValueError(f"Unable to get {app} leader")
    try:
        action_result = jhelper.run_action(unit, model, action_cmd)
    except juju.ActionFailedException:
        _message = "Unable to retrieve URL from Horizon service"
        raise ValueError(_message)
    return action_result["url"]


@click.group()
@click.pass_context
def dashboard(ctx: click.Context) -> None:
    """Manage OpenStack Dashboard."""


@dashboard.command("url")
@click.pass_context
def dashboard_url(ctx: click.Context) -> None:
    """Retrieve OpenStack Dashboard URL."""
    deployment: Deployment = ctx.obj
    preflight_checks = [
        VerifyBootstrappedCheck(deployment.get_client()),
        JujuLoginCheck(deployment.juju_account),
    ]
    run_preflight_checks(preflight_checks, console)

    jhelper = juju.JujuHelper(deployment.juju_controller)

    with console.status("Retrieving dashboard URL from Horizon service ... "):
        try:
            console.print(retrieve_dashboard_url(jhelper))
        except Exception as e:
            raise click.ClickException(str(e))


@click.group()
@click.pass_context
def theme(ctx: click.Context) -> None:
    """Manage Horizon themes."""


dashboard.add_command(theme)


@theme.command("set")
@click_option_show_hints
@click.pass_context
def set_theme(ctx: click.Context, show_hints: bool) -> None:
    """Set a custom Horizon theme interactively."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.juju_controller)
    manifest = deployment.get_manifest()
    tfhelper = deployment.get_tfhelper("openstack-plan")

    plan = [
        TerraformInitStep(tfhelper),
        AttachHorizonThemeStep(
            client=client,
            jhelper=jhelper,
            tfhelper=tfhelper,
            manifest=manifest,
            model=OPENSTACK_MODEL,
            prompt_mode=PromptMode.FORCE,
        ),
    ]
    run_plan(plan, console, show_hints)
    console.print("Custom theme applied.")


@theme.command("clear")
@click_option_show_hints
@click.pass_context
def clear_theme(ctx: click.Context, show_hints: bool) -> None:
    """Clear custom Horizon theme and restore defaults."""
    deployment: Deployment = ctx.obj
    client = deployment.get_client()
    jhelper = JujuHelper(deployment.juju_controller)
    manifest = deployment.get_manifest()
    tfhelper = deployment.get_tfhelper("openstack-plan")

    write_answers(client, THEME_CONFIG_SECTION, {"enable_custom_theme": False})

    plan = [
        TerraformInitStep(tfhelper),
        AttachHorizonThemeStep(
            client=client,
            jhelper=jhelper,
            tfhelper=tfhelper,
            manifest=manifest,
            model=OPENSTACK_MODEL,
            prompt_mode=PromptMode.NEVER,
        ),
    ]
    run_plan(plan, console, show_hints)
    console.print("Custom theme cleared.")
