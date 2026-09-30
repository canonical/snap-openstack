# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""``sunbeam backup``, ``sunbeam restore`` and ``sunbeam list-backups`` commands."""

import logging
import sys
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from sunbeam.core.common import get_step_message, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import ConfirmQuestion, Question
from sunbeam.steps.backup_restore import (
    BACKUP_COMPONENTS,
    DEFAULT_BACKUP_TIMEOUT,
    DEFAULT_RESTORE_TIMEOUT,
    RESTORE_TIME_FORMAT,
    BackupInventory,
    BackupResult,
    DiscoverBackupApplicationsStep,
    ListBackupsStep,
    ResolveActionTargetsStep,
    RestoreResult,
    RestoreStep,
    RunBackupStep,
    ValidateStep,
    WriteBackupInventoryManifestStep,
    WriteBackupManifestStep,
)

LOG = logging.getLogger(__name__)
console = Console()

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FAILURE = 2

CONTINUE_BACKUP_QUESTION = ConfirmQuestion(
    "Continue and back up the remaining components?",
    default_value=False,
    description=(
        "Some discovered applications were skipped because they are not"
        " ready for backup."
    ),
)

CONTINUE_RESTORE_READY_QUESTION = ConfirmQuestion(
    "Continue and restore the remaining components?",
    default_value=False,
    description=(
        "Some discovered applications were skipped because they are not"
        " ready for restore. Partial restores may result in "
        "dangling OpenStack objects. "
    ),
)

CONTINUE_RESTORE_FILTER_QUESTION = ConfirmQuestion(
    "Continue and restore the remaining components?",
    default_value=False,
    description=(
        "Some discovered applications have missing or failed backups.\n"
        "- Applications without successful backups will be skipped.\n"
        "- Applications with mixed backup results will restore from successful "
        "backups only.\n"
        "Partial restores may result in dangling OpenStack objects."
    ),
)

START_RESTORE_QUESTION = ConfirmQuestion(
    "Start restore?",
    default_value=False,
)


def _confirm_or_abort(question: Question, no_prompt: bool) -> None:
    """Ask a confirmation question, aborting the command if declined."""
    if no_prompt:
        return
    question.console = console
    question.show_hint = True
    if not question.ask():
        raise click.Abort()


def _components_banner() -> str:
    return ",".join(c.name for c in BACKUP_COMPONENTS)


def _discover_apps(jhelper, model: str) -> dict[str, list[str]]:
    """Discover applications for every registered backup component."""
    results = run_plan(
        [DiscoverBackupApplicationsStep(jhelper, model, BACKUP_COMPONENTS)], console
    )
    return get_step_message(results, DiscoverBackupApplicationsStep)


def _validate_apps(
    jhelper,
    discovered: dict[str, list[str]],
    model: str,
    force: bool,
) -> tuple[dict[str, list[str]], bool]:
    """Run the validation step and warn on every skipped application."""
    results = run_plan(
        [ValidateStep(jhelper, discovered, model=model, force=force)], console
    )
    outcome = get_step_message(results, ValidateStep)
    valid = outcome["valid"]
    failures = outcome["failures"]

    for app in sorted(failures):
        reasons = ", ".join(failures[app])
        console.print(
            f"[yellow]Warning:[/yellow] {app} is not ready for backup"
            f" ({reasons}) and will be skipped."
        )

    return valid, bool(failures)


def _list_backup_inventory(
    jhelper,
    discovered: dict[str, list[str]],
    model: str,
    timeout: int,
) -> list[BackupInventory]:
    """List available backups for the given targets."""
    results = run_plan(
        [
            ResolveActionTargetsStep(
                jhelper,
                discovered,
                action=lambda component: component.list_action,
                model=model,
            )
        ],
        console,
    )
    resolved = get_step_message(results, ResolveActionTargetsStep)
    targets = resolved["targets"]
    unresolved_targets = resolved["unresolved"]

    results = run_plan(
        [
            ListBackupsStep(
                jhelper,
                targets,
                timeout=timeout,
                model=model,
            )
        ],
        console,
    )
    inventories = get_step_message(results, ListBackupsStep)
    for unresolved in unresolved_targets:
        inventories.append(
            BackupInventory(
                app=unresolved["app"],
                unit="-",
                component=unresolved["component"],
                error="Could not resolve target.",
            )
        )
    return inventories


def _print_inventory(results: list[BackupInventory]) -> None:
    table = Table()
    table.add_column("Application")
    table.add_column("Component")
    table.add_column("Backup IDs")
    table.add_column("Status")

    for result in sorted(results, key=lambda inventory: inventory.app):
        if result.backups:
            ordered = sorted(result.backups, key=lambda b: b.backup_id, reverse=True)
            backup_ids = "\n".join(b.backup_id for b in ordered)
            statuses = "\n".join(
                "[green]ok[/green]"
                if b.success is True
                else "[red]failed[/red]"
                if b.success is False
                else "-"
                for b in ordered
            )
        else:
            backup_ids = "-"
            statuses = "[red]failed[/red]" if result.error else "-"
        table.add_row(result.app, result.component, backup_ids, statuses)

    console.print(table)


def _print_backup_summary(results: list[BackupResult]) -> None:
    table = Table()
    table.add_column("Application")
    table.add_column("Component")
    table.add_column("Backup ID")
    table.add_column("Status")
    for result in results:
        if result.backup is None:
            table.add_row(result.app, result.component, "-", "-")
            continue
        status = (
            "[green]ok[/green]"
            if result.backup.success is True
            else "[red]failed[/red]"
            if result.backup.success is False
            else "-"
        )
        table.add_row(
            result.app, result.component, result.backup.backup_id or "-", status
        )
    console.print(table)


def _filter_restore_targets(
    discovered: dict[str, list[str]],
    inventory: list[BackupInventory],
) -> tuple[dict[str, list[str]], bool]:
    """Warn on missing inventory and keep only restorable targets."""
    partially_failed_apps = {
        entry.app
        for entry in inventory
        if entry.backups
        and any(b.success is True for b in entry.backups)
        and any(b.success is False for b in entry.backups)
    }

    failed_inventory_by_app: dict[str, BackupInventory] = {
        entry.app: entry
        for entry in inventory
        if entry.error is not None
        or not entry.backups
        or not any(b.success is True for b in entry.backups)
    }

    for entry, inv in failed_inventory_by_app.items():
        if inv.error:
            console.print(
                "[yellow]Warning:[/yellow] Failed to list backups for "
                f"{entry}: {inv.error}"
            )
        elif not inv.backups:
            console.print(
                f"[yellow]Warning:[/yellow] No backups available for {entry}."
            )
        elif any(b.success is False for b in inv.backups):
            console.print(
                f"[yellow]Warning:[/yellow] Some backups failed for {entry}."
                f" No successful backups are available for restore."
            )
        else:
            console.print(
                f"[yellow]Warning:[/yellow] No successful backups available for"
                f" {entry}."
            )

    for app in sorted(partially_failed_apps):
        console.print(
            f"[yellow]Warning:[/yellow] Some backups for {app} failed."
            " Only successful backups will be considered for restore"
            " (possible out-of-band state)."
        )

    restorable: dict[str, list[str]] = {}
    for component, apps in discovered.items():
        for app in apps:
            if app in failed_inventory_by_app:
                continue
            restorable.setdefault(component, []).append(app)
    return restorable, bool(failed_inventory_by_app) or bool(partially_failed_apps)


def _warn_restore_to_time_fallback_targets(
    discovered: dict[str, list[str]],
    restore_to_time: str | None,
) -> list[str]:
    """When PITR is requested, report apps that will fall back to latest backup."""
    if restore_to_time is None:
        return []

    supports_restore_to_time = {
        component.name: component.restore_to_time_param is not None
        for component in BACKUP_COMPONENTS
    }

    fallback_apps: list[str] = []
    for component, apps in discovered.items():
        if supports_restore_to_time.get(component, False):
            continue
        fallback_apps.extend(apps)

    return sorted(fallback_apps)


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
    deployment: Deployment = ctx.obj
    jhelper = deployment.get_juju_helper()
    model = OPENSTACK_MODEL

    console.print(
        f"[bold]Backing up \\[{_components_banner()}] in model '{model}'...[/bold]"
    )

    discovered = _discover_apps(jhelper, model)
    if not any(discovered.values()):
        console.print("No applications found to back up. Exiting.")
        sys.exit(EXIT_FAILURE)

    discovered, was_filtered = _validate_apps(jhelper, discovered, model, force=force)
    if was_filtered:
        _confirm_or_abort(CONTINUE_BACKUP_QUESTION, no_prompt)

    if not any(discovered.values()):
        console.print("No applications remain to back up after validation. Exiting.")
        sys.exit(EXIT_FAILURE)

    dispatched_at = datetime.now(timezone.utc).strftime(RESTORE_TIME_FORMAT)
    console.print(f"Dispatching backups at {dispatched_at} UTC...")
    backup_results = run_plan(
        [RunBackupStep(jhelper, discovered, force=force, timeout=timeout, model=model)],
        console,
    )
    results: list[BackupResult] = get_step_message(backup_results, RunBackupStep)

    if not results:
        console.print(
            "Could not resolve a backup target for any application. Re-run with"
            " --force to back up on leader units regardless of cluster health."
        )
        sys.exit(EXIT_FAILURE)

    _print_backup_summary(results)

    manifest_results = run_plan(
        [WriteBackupManifestStep(results, dispatched_at)], console
    )
    manifest_path = get_step_message(manifest_results, WriteBackupManifestStep)
    if manifest_path:
        console.print(f"Backup manifest written to: {manifest_path}")

    succeeded = sum(1 for r in results if r.backup is not None and r.backup.success)
    failed = sum(1 for r in results if r.error is not None)
    console.print(f"Backup summary: {succeeded} succeeded, {failed} failed.")

    if failed == 0:
        sys.exit(EXIT_SUCCESS)

    console.print(
        "[yellow]Warning:[/yellow] one or more backups failed or timed out. A partial"
        " restore from a set may result in dangling OpenStack objects."
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
    model = OPENSTACK_MODEL

    console.print(
        f"[bold]Listing backups for \\[{_components_banner()}]"
        f" in model '{model}'...[/bold]"
    )

    discovered = _discover_apps(jhelper, model)
    discovered, _ = _validate_apps(jhelper, discovered, model, force=False)

    if not any(discovered.values()):
        console.print("No applications found to list backups from. Exiting.")
        sys.exit(EXIT_FAILURE)

    listed_at = datetime.now(timezone.utc).strftime(RESTORE_TIME_FORMAT)
    inventory = _list_backup_inventory(jhelper, discovered, model, timeout)

    failed_inventory = sorted(
        (entry for entry in inventory if entry.error),
        key=lambda entry: entry.app,
    )
    for entry in failed_inventory:
        console.print(
            f"[yellow]Warning:[/yellow] Failed to list backups for"
            f" {entry.app}: {entry.error}"
        )

    _print_inventory(inventory)

    manifest_results = run_plan(
        [WriteBackupInventoryManifestStep(inventory, listed_at)], console
    )
    manifest_path = get_step_message(manifest_results, WriteBackupInventoryManifestStep)
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
    model = OPENSTACK_MODEL

    console.print(
        f"[bold]Restoring \\[{_components_banner()}]"
        f" in model '{model}' from backup...[/bold]"
    )

    discovered = _discover_apps(jhelper, model)
    if not any(discovered.values()):
        console.print("No applications found to restore. Exiting.")
        sys.exit(EXIT_FAILURE)

    discovered, was_filtered = _validate_apps(jhelper, discovered, model, force=force)
    if was_filtered:
        _confirm_or_abort(CONTINUE_RESTORE_READY_QUESTION, no_prompt)

    inventory = _list_backup_inventory(jhelper, discovered, model, timeout)
    _print_inventory(inventory)

    unresolved_restore_targets = [
        entry
        for entry in inventory
        if entry.unit == "-" and entry.error == "Could not resolve target."
    ]
    if unresolved_restore_targets:
        for entry in unresolved_restore_targets:
            console.print(
                f"[red]Error:[/red] Could not resolve restore target for {entry.app}."
            )
        sys.exit(EXIT_FAILURE)

    discovered, was_filtered = _filter_restore_targets(discovered, inventory)

    if was_filtered:
        _confirm_or_abort(CONTINUE_RESTORE_FILTER_QUESTION, no_prompt)

    if not any(discovered.values()):
        console.print("No applications remain to restore after validation. Exiting.")
        sys.exit(EXIT_FAILURE)

    fallback_for_restore_to_time = _warn_restore_to_time_fallback_targets(
        discovered, restore_to_time
    )
    for app in fallback_for_restore_to_time:
        console.print(
            f"[yellow]Warning:[/yellow] {app} does not support"
            " --restore-to-time. Restoring latest available backup instead."
        )

    if restore_to_time:
        START_RESTORE_QUESTION.description = (
            f"Restore will be performed to the point-in-time {restore_to_time} UTC."
        )
    else:
        START_RESTORE_QUESTION.description = (
            "Restore will be performed to the latest available backup."
        )
    _confirm_or_abort(START_RESTORE_QUESTION, no_prompt)

    restore_results = run_plan(
        [
            RestoreStep(
                jhelper,
                discovered,
                restore_to_time=restore_to_time,
                timeout=timeout,
                model=model,
            )
        ],
        console,
    )
    results: list[RestoreResult] = get_step_message(restore_results, RestoreStep)

    for result in results:
        if not result.success:
            reverted = " Reverted." if result.reverted else ""
            rollback_failed = (
                f" Rollback failed: {result.rollback_error}."
                if result.rollback_error
                else ""
            )
            console.print(
                f"[red]Error:[/red] {result.app} restore failed:"
                f" {result.error}.{reverted}{rollback_failed}"
            )

    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    console.print(f"Restore summary: {succeeded} succeeded, {failed} failed.")

    if failed == 0:
        sys.exit(EXIT_SUCCESS)
    if succeeded == 0:
        sys.exit(EXIT_FAILURE)
    sys.exit(EXIT_PARTIAL)
