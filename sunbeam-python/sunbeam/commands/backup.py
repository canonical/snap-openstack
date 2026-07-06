# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""``sunbeam backup`` command."""

import logging
import sys
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from sunbeam.core.common import get_step_message, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.steps.backup import (
    BACKUP_COMPONENTS,
    DEFAULT_BACKUP_TIMEOUT,
    VAULT_PREREQUISITE_MSG,
    BackupResult,
    BackupTarget,
    DiscoverBackupApplicationsStep,
    ResolveBackupTargetsStep,
    RunBackupsStep,
    WriteBackupManifestStep,
)

LOG = logging.getLogger(__name__)
console = Console()

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILURE = 2


def _print_summary(results: list[BackupResult]) -> None:
    table = Table()
    table.add_column("Application")
    table.add_column("Unit")
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Backup ID")
    for result in results:
        status = "[green]done[/green]" if result.success else "[red]failed[/red]"
        table.add_row(
            result.app,
            result.unit,
            result.component,
            status,
            result.backup_id or "-",
        )
    console.print(table)


@click.command()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "Back up applications whose cluster health cannot be verified, using the"
        " leader unit. May capture stale data; use with caution."
    ),
)
@click.option(
    "--timeout",
    default=DEFAULT_BACKUP_TIMEOUT,
    show_default=True,
    help="Time in seconds to wait for each backup to complete.",
)
@click.option(
    "--no-prompt", is_flag=True, default=False, help="Do not prompt for confirmation."
)
@click.pass_context
def backup(ctx: click.Context, force: bool, timeout: int, no_prompt: bool) -> None:
    """Create backups of stateful Sunbeam applications (MySQL and Vault)."""
    if not no_prompt:
        click.confirm(VAULT_PREREQUISITE_MSG, abort=True)

    deployment: Deployment = ctx.obj
    jhelper = deployment.get_juju_helper()
    model = deployment.openstack_machines_model

    console.print(
        f"[bold]Backing up [{','.join(c.name for c in BACKUP_COMPONENTS)}]"
        f" in model '{model}'...[/bold]"
    )

    discover_step = DiscoverBackupApplicationsStep(jhelper, model, BACKUP_COMPONENTS)
    resolve_results = run_plan([discover_step], console)
    discovered = get_step_message(resolve_results, DiscoverBackupApplicationsStep)

    if not any(discovered.values()):
        console.print(
            "No MySQL or Vault applications found. Nothing to back up. Check that"
            " the applications are deployed in the model."
        )
        sys.exit(EXIT_FAILURE)

    resolve_step = ResolveBackupTargetsStep(
        jhelper, discovered, model=model, force=force
    )
    targets_results = run_plan([resolve_step], console)
    targets: list[BackupTarget] = get_step_message(
        targets_results, ResolveBackupTargetsStep
    )

    if not targets:
        console.print(
            "Could not resolve a backup target for any application. Re-run with"
            " --force to back up on leader units regardless of cluster health."
        )
        sys.exit(EXIT_FAILURE)

    dispatched_at = datetime.now(timezone.utc).isoformat()
    console.print(f"Dispatching backups at {dispatched_at}...")
    run_step = RunBackupsStep(
        jhelper, targets, force=force, timeout=timeout, model=model
    )
    backup_results = run_plan([run_step], console)
    results: list[BackupResult] = get_step_message(backup_results, RunBackupsStep)

    _print_summary(results)

    manifest_step = WriteBackupManifestStep(results, dispatched_at)
    manifest_results = run_plan([manifest_step], console)
    manifest_path = get_step_message(manifest_results, WriteBackupManifestStep)
    if manifest_path:
        console.print(f"Backup manifest written to: {manifest_path}")

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded
    console.print(f"Backup summary: {succeeded} succeeded, {failed} failed.")

    if failed == 0:
        sys.exit(EXIT_SUCCESS)

    if any(r.component == "mysql" and not r.success for r in results):
        console.print(
            "[yellow]Warning:[/yellow] one or more MySQL backups failed. A partial"
            " restore from this set may result in dangling OpenStack objects."
        )

    if succeeded == 0:
        sys.exit(EXIT_FAILURE)
    sys.exit(EXIT_PARTIAL)
