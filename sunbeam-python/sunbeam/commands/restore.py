# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""``sunbeam restore`` command.

This command lays out the in-place restore sequence. The steps that depend on the
control-plane pause/resume charm actions are guarded, so invoking restore today
stops before any destructive action and reports a clear failure.
"""

import logging
from datetime import datetime

import click
from rich.console import Console

from sunbeam.core.common import BaseStep, get_step_message, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.steps.backup import (
    BACKUP_COMPONENTS,
    VAULT_PREREQUISITE_MSG,
    BackupTarget,
    DiscoverBackupApplicationsStep,
    ResolveBackupTargetsStep,
)
from sunbeam.steps.restore import (
    DEFAULT_RESTORE_TIMEOUT,
    DEFAULT_SCALE_TIMEOUT,
    PauseControlPlaneStep,
    RestoreMySQLStep,
    RestoreVaultStep,
    ResumeControlPlaneStep,
    ScaleMySQLStep,
)

LOG = logging.getLogger(__name__)
console = Console()

RESTORE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _validate_restore_to_time(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    if value is None:
        return None
    try:
        datetime.strptime(value, RESTORE_TIME_FORMAT)
    except ValueError:
        raise click.BadParameter(
            f"expected format 'YYYY-MM-DD HH:MM:SS', got {value!r}"
        )
    return value


@click.command()
@click.option(
    "--restore-to-time",
    default=None,
    callback=_validate_restore_to_time,
    help="Point-in-time to restore to, formatted 'YYYY-MM-DD HH:MM:SS'.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    show_default=True,
    help="Proceed with restore despite cluster health concerns.",
)
@click.option(
    "--timeout",
    default=DEFAULT_RESTORE_TIMEOUT,
    show_default=True,
    help="Time in seconds to wait for restore operations to complete.",
)
@click.option(
    "--no-prompt", is_flag=True, default=False, help="Do not prompt for confirmation."
)
@click.pass_context
def restore(
    ctx: click.Context,
    restore_to_time: str | None,
    force: bool,
    timeout: int,
    no_prompt: bool,
) -> None:
    """Restore stateful Sunbeam applications from a backup."""
    if not no_prompt:
        click.confirm(VAULT_PREREQUISITE_MSG, abort=True)

    deployment: Deployment = ctx.obj
    jhelper = deployment.get_juju_helper()
    model = deployment.openstack_machines_model

    console.print(
        f"[bold]Restoring [{','.join(c.name for c in BACKUP_COMPONENTS)}]"
        f" in model '{model}' from backup...[/bold]"
    )

    discover_results = run_plan(
        [DiscoverBackupApplicationsStep(jhelper, model, BACKUP_COMPONENTS)], console
    )
    discovered = get_step_message(discover_results, DiscoverBackupApplicationsStep)

    resolve_results = run_plan(
        [ResolveBackupTargetsStep(jhelper, discovered, model=model, force=force)],
        console,
    )
    targets: list[BackupTarget] = get_step_message(
        resolve_results, ResolveBackupTargetsStep
    )
    mysql_targets = [t for t in targets if t.component == "mysql"]

    # Restore plan
    plan: list[BaseStep] = [PauseControlPlaneStep(jhelper, model=model)]
    for target in mysql_targets:
        plan.append(
            ScaleMySQLStep(
                jhelper, target.app, 1, timeout=DEFAULT_SCALE_TIMEOUT, model=model
            )
        )
        plan.append(
            RestoreMySQLStep(
                jhelper,
                target,
                restore_to_time=restore_to_time,
                timeout=timeout,
                model=model,
            )
        )
        plan.append(
            ScaleMySQLStep(
                jhelper,
                target.app,
                target.scale,
                timeout=DEFAULT_SCALE_TIMEOUT,
                model=model,
            )
        )
    plan.append(RestoreVaultStep(jhelper, timeout=timeout, model=model))
    plan.append(ResumeControlPlaneStep(jhelper, model=model))

    run_plan(plan, console)
