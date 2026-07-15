# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""``sunbeam backup``, ``sunbeam restore`` and ``sunbeam list-backups`` commands."""

import logging
import sys
from datetime import datetime, timezone
from typing import Tuple

import click
from rich.console import Console
from rich.table import Table

from sunbeam.core.common import BaseStep, get_step_message, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.steps.backup_restore import (
    BACKUP_COMPONENTS,
    DEFAULT_BACKUP_TIMEOUT,
    DEFAULT_RESTORE_TIMEOUT,
    DEFAULT_SCALE_TIMEOUT,
    MYSQL_S3_RELATION,
    RESTORE_TIME_FORMAT,
    VAULT_PREREQUISITE_MSG,
    VAULT_S3_RELATION,
    BackupInventory,
    BackupResult,
    BackupTarget,
    CheckAppPauseResumeSupportStep,
    CheckS3RelationsStep,
    DiscoverBackupApplicationsStep,
    ListBackupsStep,
    PauseAppStep,
    ResolveBackupTargetsStep,
    RestoreMySQLStep,
    RestoreVaultStep,
    ResumeAppStep,
    RunBackupsStep,
    ScaleAppStep,
    WriteBackupInventoryManifestStep,
    WriteBackupManifestStep,
)

LOG = logging.getLogger(__name__)
console = Console()

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILURE = 2

COMPONENT_LABELS = {"mysql": "MySQL", "vault": "Vault"}

S3_RELATIONS = (
    ("mysql", MYSQL_S3_RELATION),
    ("vault", VAULT_S3_RELATION),
)


def _discover_applications(
    console_: Console, jhelper, model: str, components
) -> dict[str, list[str]]:
    """Discover applications for every registered backup component."""
    results = run_plan(
        [DiscoverBackupApplicationsStep(jhelper, model, components)], console_
    )
    return get_step_message(results, DiscoverBackupApplicationsStep)


def _filter_s3_related_apps(
    console_: Console,
    jhelper,
    discovered: dict[str, list[str]],
    model: str,
) -> Tuple[dict[str, list[str]], bool]:
    """Filter discovered apps to those related to the required S3 endpoints."""
    was_filtered = False
    filtered: dict[str, list[str]] = {}

    for component_name, endpoint_name in S3_RELATIONS:
        applications = discovered.get(component_name, [])
        if not applications:
            continue

        s3_check_results = run_plan(
            [
                CheckS3RelationsStep(
                    jhelper,
                    applications,
                    endpoint_name=endpoint_name,
                    model=model,
                )
            ],
            console_,
        )
        s3_relations = get_step_message(s3_check_results, CheckS3RelationsStep)
        unrelated_apps = s3_relations["unrelated"]
        filtered[component_name] = s3_relations["related"]

        if unrelated_apps:
            was_filtered = True
            label = COMPONENT_LABELS.get(component_name, component_name)
            console_.print(
                f"[yellow]Warning:[/yellow] the following {label} applications are"
                f" not related via '{endpoint_name}' and will be skipped:"
                f" {', '.join(unrelated_apps)}"
            )

    return filtered, was_filtered


def _resolve_targets(
    console_: Console,
    jhelper,
    discovered: dict[str, list[str]],
    model: str,
    force: bool,
) -> list[BackupTarget]:
    """Resolve the unit to act on for every discovered application."""
    results = run_plan(
        [ResolveBackupTargetsStep(jhelper, discovered, model=model, force=force)],
        console_,
    )
    return get_step_message(results, ResolveBackupTargetsStep)


def _list_inventory(
    console_: Console,
    jhelper,
    targets: list[BackupTarget],
    model: str,
    timeout: int,
) -> list[BackupInventory]:
    """List available backups for the given targets."""
    results = run_plan(
        [ListBackupsStep(jhelper, targets, timeout=timeout, model=model)], console_
    )
    return get_step_message(results, ListBackupsStep)


def _print_inventory(results: list[BackupInventory]) -> None:
    table = Table()
    table.add_column("Application")
    table.add_column("Unit")
    table.add_column("Component")
    table.add_column("Backup IDs")
    table.add_column("Status")

    for result in sorted(results, key=lambda inventory: inventory.app):
        status = "[green]done[/green]" if result.success else "[red]failed[/red]"
        backup_ids = "-"
        if result.success and result.backup_ids:
            backup_ids = "\n".join(result.backup_ids)
        table.add_row(result.app, result.unit, result.component, backup_ids, status)

    console.print(table)


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


def _filter_restore_targets(
    targets: list[BackupTarget],
    inventory: list[BackupInventory],
) -> list[BackupTarget]:
    """Warn on missing inventory and keep only restorable targets."""
    inventory_by_app = {entry.app: entry for entry in inventory}
    failed_inventory = sorted(
        (entry for entry in inventory if not entry.success or not entry.backup_ids),
        key=lambda entry: entry.app,
    )
    for entry in failed_inventory:
        if not entry.success:
            details = f": {entry.error}" if entry.error else ""
            console.print(
                "[yellow]Warning:[/yellow] Failed to list backups for "
                f"{entry.app}{details}"
            )
        else:
            console.print(
                f"[yellow]Warning:[/yellow] No backups available for {entry.app}."
            )

    return [
        target
        for target in targets
        if (inventory_by_app.get(target.app) is not None)
        and inventory_by_app[target.app].success
        and inventory_by_app[target.app].backup_ids
    ]


def _run_mysql_restore(
    jhelper,
    target: BackupTarget,
    model: str,
    restore_to_time: str | None,
    timeout: int,
) -> None:
    """Run the restore plan for a MySQL target."""
    app_plan = [
        PauseAppStep(jhelper, target.app, model=model),
        ScaleAppStep(
            jhelper,
            target.app,
            1,
            timeout=DEFAULT_SCALE_TIMEOUT,
            model=model,
        ),
        RestoreMySQLStep(
            jhelper,
            target,
            restore_to_time=restore_to_time,
            timeout=timeout,
            model=model,
        ),
        ScaleAppStep(
            jhelper,
            target.app,
            target.scale,
            timeout=DEFAULT_SCALE_TIMEOUT,
            model=model,
        ),
        ResumeAppStep(jhelper, target.app, model=model),
    ]

    try:
        run_plan(app_plan, console)
    except click.ClickException as e:
        revert_plan = [
            ScaleAppStep(
                jhelper,
                target.app,
                target.scale,
                timeout=DEFAULT_SCALE_TIMEOUT,
                model=model,
            ),
            ResumeAppStep(jhelper, target.app, model=model),
        ]
        try:
            run_plan(revert_plan, console)
        except click.ClickException:
            pass

        raise click.ClickException(
            f"{target.app} restore failed: {e.message or str(e)}. Attempted to revert."
        )


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

    discovered = _discover_applications(console, jhelper, model, BACKUP_COMPONENTS)
    if not any(discovered.values()):
        console.print("No applications found to back up. Exiting.")
        sys.exit(EXIT_FAILURE)

    discovered, was_filtered = _filter_s3_related_apps(
        console, jhelper, discovered, model
    )
    if was_filtered and not no_prompt:
        click.confirm(
            "Continue and back up the remaining components?",
            abort=True,
        )

    targets: list[BackupTarget] = _resolve_targets(
        console, jhelper, discovered, model, force=force
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


@click.command("list-backups")
@click.option(
    "--timeout",
    default=DEFAULT_BACKUP_TIMEOUT,
    show_default=True,
    help="Time in seconds to wait for each list action to complete.",
)
@click.pass_context
def list_backups(ctx: click.Context, timeout: int) -> None:
    """List backup IDs from stateful Sunbeam applications."""
    deployment: Deployment = ctx.obj
    jhelper = deployment.get_juju_helper()
    model = deployment.openstack_machines_model

    console.print(
        f"[bold]Listing backups for [{','.join(c.name for c in BACKUP_COMPONENTS)}]"
        f" in model '{model}'...[/bold]"
    )

    discovered = _discover_applications(console, jhelper, model, BACKUP_COMPONENTS)
    discovered, _ = _filter_s3_related_apps(console, jhelper, discovered, model)

    if not any(discovered.values()):
        console.print("No applications found to list backups from. Exiting.")
        sys.exit(EXIT_FAILURE)

    targets: list[BackupTarget] = _resolve_targets(
        console, jhelper, discovered, model, force=False
    )

    listed_at = datetime.now(timezone.utc).isoformat()
    inventory: list[BackupInventory] = _list_inventory(
        console, jhelper, targets, model, timeout
    )

    failed_inventory = sorted(
        (entry for entry in inventory if not entry.success),
        key=lambda entry: entry.app,
    )
    for entry in failed_inventory:
        details = f": {entry.error}" if entry.error else ""
        console.print(
            f"[yellow]Warning:[/yellow] Failed to list backups for {entry.app}{details}"
        )

    _print_inventory(inventory)

    manifest_step = WriteBackupInventoryManifestStep(inventory, listed_at)
    manifest_results = run_plan([manifest_step], console)
    manifest_path = get_step_message(
        manifest_results,
        WriteBackupInventoryManifestStep,
    )
    if manifest_path:
        console.print(f"Backup inventory manifest written to: {manifest_path}")

    if failed_inventory:
        sys.exit(EXIT_FAILURE)

    sys.exit(EXIT_SUCCESS)


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
    deployment: Deployment = ctx.obj
    jhelper = deployment.get_juju_helper()
    model = deployment.openstack_machines_model

    console.print(
        f"[bold]Restoring [{','.join(c.name for c in BACKUP_COMPONENTS)}]"
        f" in model '{model}' from backup...[/bold]"
    )

    discovered = _discover_applications(console, jhelper, model, BACKUP_COMPONENTS)
    if not any(discovered.values()):
        console.print("No applications found to restore. Exiting.")
        sys.exit(EXIT_FAILURE)

    discovered, was_filtered = _filter_s3_related_apps(
        console, jhelper, discovered, model
    )
    if was_filtered and not no_prompt:
        click.confirm(
            "Continue and restore the remaining components?",
            abort=True,
        )

    targets: list[BackupTarget] = _resolve_targets(
        console, jhelper, discovered, model, force=force
    )
    if not targets:
        console.print("No restore targets could be resolved. Exiting.")
        sys.exit(EXIT_FAILURE)

    inventory: list[BackupInventory] = _list_inventory(
        console, jhelper, targets, model, timeout
    )
    targets = _filter_restore_targets(targets, inventory)

    if not targets:
        console.print("No backups were found to restore from. Exiting.")
        sys.exit(EXIT_FAILURE)

    if restore_to_time is not None and any(t.component == "vault" for t in targets):
        console.print(
            "[yellow]Warning:[/yellow] Vault does not support point-in-time restore;"
            " the latest Vault backup will be used."
        )

    if any(t.component == "vault" for t in targets) and not no_prompt:
        click.confirm(VAULT_PREREQUISITE_MSG, abort=True)

    mysql_targets = [t for t in targets if t.component == "mysql"]
    vault_targets = [t for t in targets if t.component == "vault"]

    precheck_plan: list[BaseStep] = []
    apps_to_pause_resume = sorted({target.app for target in mysql_targets})
    if apps_to_pause_resume:
        precheck_plan.append(
            CheckAppPauseResumeSupportStep(
                jhelper,
                apps_to_pause_resume,
                model=model,
            )
        )

    if precheck_plan:
        run_plan(precheck_plan, console)

    for target in mysql_targets:
        _run_mysql_restore(
            jhelper,
            target,
            model,
            restore_to_time,
            timeout,
        )

    vault_plan: list[BaseStep] = []
    for target in vault_targets:
        vault_plan.append(
            RestoreVaultStep(jhelper, target, timeout=timeout, model=model)
        )

    if vault_targets:
        run_plan(vault_plan, console)
