# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Upgrade CLI commands.

Provides the ``sunbeam cluster upgrade`` command group with
subcommands for managing the upgrade lifecycle.

Current subcommands:
- preflight: run pre-flight checks and create the active hop.
- abandon: mark the active hop as abandoned, release the lock, and
  print recovery guidance.
- control-plane: upgrade control-plane charm groups.
"""

from __future__ import annotations

import click
from rich.console import Console

from sunbeam.clusterd.service import UpgradeLockHeldException
from sunbeam.core.deployment import Deployment
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.upgrades.control_plane.groups import ControlPlaneHandler
from sunbeam.upgrades.coordinator import ReleaseUpgradeCoordinator
from sunbeam.upgrades.metadata import load_upgrade_metadata
from sunbeam.upgrades.observability import UpgradeLogger
from sunbeam.upgrades.preflight.checks import (
    CheckContext,
    build_preflight_checks,
    run_upgrade_preflight_checks,
)
from sunbeam.upgrades.preflight.hop import create_hop_after_preflight
from sunbeam.upgrades.state import PhaseStatus
from sunbeam.versions import detect_deployed_release, detect_snap_release

console = Console()


def _detect_from_release(deployment: Deployment) -> str:
    """Auto-detect the source release from deployed charm channels.

    Reads charm channels from the openstack model and matches against
    RELEASE_TRACKS. Falls back to prompting the operator if detection
    fails.
    """
    jhelper = deployment.get_juju_helper()
    status = jhelper.get_model_status(OPENSTACK_MODEL)
    charm_channels: dict[str, str] = {}
    for app_name, app in status.apps.items():
        if app.charm_channel:
            charm_channels[app_name] = app.charm_channel
    release = detect_deployed_release(charm_channels)
    if release is None:
        raise click.ClickException(
            "Cannot auto-detect the deployed OpenStack release from charm "
            "channels. Specify --from explicitly."
        )
    return release


@click.group("upgrade", context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def upgrade(ctx: click.Context) -> None:
    """Manage cluster upgrades."""


@upgrade.command("preflight")
@click.option(
    "--from",
    "from_release",
    help="Source release (auto-detected if omitted).",
    default=None,
)
@click.option(
    "--capacity-policy-override",
    is_flag=True,
    default=False,
    help="Skip the capacity policy check.",
)
@click.pass_context
def preflight(
    ctx: click.Context,
    from_release: str | None,
    capacity_policy_override: bool,
) -> None:
    """Run pre-flight checks and create the active upgrade hop.

    Validates cluster health, capacity, and metadata before starting
    an upgrade. If all checks pass, creates the active hop with status
    pending.

    Exit codes:
      0 — all checks passed, active hop created
      1 — operational failure (unhealthy cluster, capacity, MySQL)
      2 — invalid hop or missing metadata
    """
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    to_release = detect_snap_release()
    if from_release is None:
        from_release = _detect_from_release(deployment)

    click.echo(f"\nRunning pre-flight checks for {from_release} -> {to_release}...\n")

    ctx_obj = CheckContext(
        deployment=deployment,
        from_release=from_release,
        to_release=to_release,
    )
    checks = build_preflight_checks(ctx_obj, capacity_override=capacity_policy_override)

    try:
        run_upgrade_preflight_checks(checks, console)
    except click.ClickException:
        raise

    click.echo("  All checks passed.\n")
    click.echo(
        "  NOTE: Ensure backups are taken before proceeding (sunbeam backup).\n"
        "  The upgrade engine does not create or verify backups.\n"
    )

    metadata = ctx_obj.metadata
    if metadata is None:
        raise click.ClickException("Metadata not loaded after preflight checks.")

    try:
        hop = create_hop_after_preflight(client, from_release, to_release, metadata)
    except UpgradeLockHeldException:
        raise click.ClickException(
            "Cannot acquire the upgrade lock — it is held by another process. "
            "Wait for the lock to expire and retry."
        )
    except RuntimeError as e:
        raise click.ClickException(str(e))

    click.echo(f"Active hop created: {hop.from_release} -> {hop.to_release}")
    click.echo("Next: sunbeam cluster upgrade control-plane --auto")


@upgrade.command("abandon")
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
@click.pass_context
def abandon(ctx: click.Context, yes: bool) -> None:
    """Abandon the active upgrade hop.

    Marks the active hop as abandoned, releases the upgrade lock, and
    prints recovery guidance. The hop enters a terminal state — it
    cannot be resumed. Recovery is operator-driven via
    ``sunbeam restore`` and component-specific commands.
    """
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    coordinator = ReleaseUpgradeCoordinator(client, UpgradeLogger())
    coordinator.load_state()
    hop = coordinator.get_current_hop()
    if hop is None:
        raise click.ClickException("No active upgrade hop to abandon.")

    if not yes:
        click.echo(
            f"This will abandon the active upgrade hop "
            f"{hop.from_release} -> {hop.to_release}.\n"
            "The hop will enter a terminal state and cannot be resumed.\n"
            "Recovery is operator-driven via 'sunbeam restore'.\n"
        )
        click.confirm("Are you sure?", abort=True)

    try:
        coordinator.acquire_lock()
        coordinator.load_state()
        coordinator.abandon()
    except UpgradeLockHeldException:
        raise click.ClickException(
            "Cannot acquire the upgrade lock — it is held by another process. "
            "Wait for the lock to expire and retry, or identify and stop "
            "the process holding the lock."
        )
    finally:
        coordinator.release_lock()

    click.echo(
        f"\nUpgrade hop {hop.from_release} -> {hop.to_release} abandoned.\n\n"
        "Recovery steps:\n"
        "  1. Recover from failure using 'sunbeam restore' and\n"
        "     component-specific commands.\n"
        "  2. Once recovered, start a new upgrade with\n"
        "     'sunbeam cluster upgrade preflight'.\n"
    )


def _print_control_plane_status(hop, metadata, control_plane_state) -> None:
    """Print per-group control-plane upgrade status."""
    click.echo(f"\nActive hop: {hop.from_release} -> {hop.to_release}")
    click.echo("Control-plane groups:")
    for g in metadata.control_plane_groups:
        gs = control_plane_state.groups.get(g.name)
        if gs is None:
            status_str = "pending"
        elif gs.status == PhaseStatus.COMPLETED:
            status_str = f"completed ({gs.completed_at or ''})"
        elif gs.status == PhaseStatus.FAILED:
            status_str = f"FAILED ({gs.last_error.message if gs.last_error else ''})"
        elif gs.status == PhaseStatus.IN_PROGRESS:
            status_str = "in progress"
        else:
            status_str = gs.status.value
        mark = "✓" if gs and gs.status == PhaseStatus.COMPLETED else " "
        click.echo(f"  {g.name:<25} {mark} {status_str}")


def _print_dry_run(
    hop, metadata, control_plane_state, handler, group_name=None
) -> None:
    """Print what would execute, including terraform plan output."""
    click.echo(f"\nActive hop: {hop.from_release} -> {hop.to_release}")
    click.echo("DRY RUN — no changes will be made.\n")
    click.echo("Control-plane groups (will execute in order):")
    groups = metadata.control_plane_groups
    if group_name:
        groups = [g for g in groups if g.name == group_name]
        if not groups:
            click.echo(f"  Group {group_name} not found in metadata.")
            return
    total = len(groups)
    for i, g in enumerate(groups, 1):
        gs = control_plane_state.groups.get(g.name)
        if gs and gs.status == PhaseStatus.COMPLETED:
            click.echo(f"  [{i}/{total}] {g.name} (completed — will skip)")
            continue

        click.echo(f"  [{i}/{total}] {g.name}")
        for app in g.apps:
            click.echo(f"    └── {app}")
        for action in g.pre_actions:
            click.echo(f"    └── Step 1 - Pre: {action.action}")
        click.echo("    └── Step 2 - Terraform apply")
        # Run terraform plan for this group and show changes under apply
        try:
            events = handler.plan_group(g)
            changes = [
                e
                for e in events
                if e.get("@level") == "warning"
                or e.get("type") == "change"
                or "change" in e
            ]
            if changes:
                click.echo("        └── Plan changes:")
                for change in changes:
                    msg = change.get("@message", "")
                    if msg:
                        click.echo(f"            {msg}")
            else:
                click.echo("        └── Plan: no changes")
        except Exception as e:
            click.echo(f"        └── Plan failed: {e}")
        click.echo(f"    └── Step 3 - Convergence wait ({g.ready_timeout_sec}s)")
        for action in g.post_actions:
            click.echo(f"    └── Step 4 - Post: {action.action}")


def _validate_flags(
    auto: bool,
    group_name: str | None,
    app_name: str | None,
    retry_group: str | None,
    show_status: bool,
    dry_run: bool,
) -> None:
    """Validate flag combinations."""
    exclusive = [bool(group_name), bool(app_name), auto]
    if sum(exclusive) > 1:
        raise click.ClickException(
            "--group, --application, and --auto are mutually exclusive."
        )
    if retry_group and (group_name or app_name or auto):
        raise click.ClickException(
            "--retry-group cannot be combined with --group, --application, or --auto."
        )
    if not any(exclusive) and not retry_group and not show_status and not dry_run:
        raise click.ClickException(
            "One of --auto, --group, --application, --retry-group, "
            "--status, or --dry-run is required."
        )


def _resolve_charm_name(deployment: Deployment, app_name: str, metadata) -> str:
    """Resolve a juju app name to its charm name.

    Looks up the app in juju status and matches its charm against the
    control-plane groups in metadata.

    :raises click.ClickException: if the app is not found or its charm
        is not in any upgrade group.
    """
    jhelper = deployment.get_juju_helper()
    status = jhelper.get_model_status(OPENSTACK_MODEL)
    app = status.apps.get(app_name)
    if app is None:
        raise click.ClickException(
            f"Application {app_name!r} not found in model {OPENSTACK_MODEL}. "
            "Use 'juju status' to list applications."
        )
    charm = app.charm
    for g in metadata.control_plane_groups:
        if charm in g.apps:
            return charm
    raise click.ClickException(
        f"Application {app_name!r} (charm {charm!r}) is not part of any "
        "control-plane upgrade group."
    )


def _check_application_group(app_name: str, metadata, control_plane_state) -> None:
    """Reject --application on failed groups."""
    for g in metadata.control_plane_groups:
        if app_name in g.apps:
            gs = control_plane_state.groups.get(g.name)
            if gs and gs.status == PhaseStatus.FAILED:
                raise click.ClickException(
                    f"Application {app_name} belongs to group {g.name} "
                    "which is in failed state. Use --retry-group to retry "
                    "the group."
                )
            break


def _execute_control_plane(
    coordinator: ReleaseUpgradeCoordinator,
    handler: ControlPlaneHandler,
    metadata,
    auto: bool,
    group_name: str | None,
    app_name: str | None,
    retry_group: str | None,
    control_plane_state,
) -> None:
    """Acquire lock and execute the control-plane upgrade."""
    try:
        coordinator.acquire_lock()
        coordinator.load_state()

        if retry_group:
            gs = control_plane_state.groups.get(retry_group)
            if gs is None:
                raise click.ClickException(f"Group {retry_group} not found in state.")
            if gs.status not in (PhaseStatus.FAILED, PhaseStatus.BLOCKED):
                raise click.ClickException(
                    f"Group {retry_group} is not failed or blocked "
                    f"(status: {gs.status.value}). Use --group instead."
                )
            gs.status = PhaseStatus.PENDING
            coordinator.persist_state()
            result = handler.run_group(coordinator, metadata, retry_group)
        elif group_name:
            result = handler.run_group(coordinator, metadata, group_name)
        elif app_name:
            result = handler.run_application(coordinator, metadata, app_name)
        elif auto:
            state = coordinator.state
            if state is None:
                raise click.ClickException("No state loaded.")
            result = handler.run(coordinator, metadata, state)
        else:
            return

        if not result.success:
            raise click.ClickException(
                result.error_message or "Control-plane upgrade failed."
            )

    except UpgradeLockHeldException:
        raise click.ClickException(
            "Cannot acquire the upgrade lock — it is held by another process."
        )
    finally:
        coordinator.release_lock()

    click.echo("Control-plane upgrade completed.")


@upgrade.command("control-plane")
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Upgrade all remaining groups in order.",
)
@click.option(
    "--group",
    "group_name",
    default=None,
    help="Upgrade a single group by name.",
)
@click.option(
    "--application",
    "app_name",
    default=None,
    help="Upgrade a single application by name (as shown in 'juju status').",
)
@click.option(
    "--status",
    "show_status",
    is_flag=True,
    default=False,
    help="Show per-group upgrade status.",
)
@click.option(
    "--retry-group",
    "retry_group",
    default=None,
    help="Retry a failed or blocked group.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would execute without making changes.",
)
@click.pass_context
def control_plane(
    ctx: click.Context,
    auto: bool,
    group_name: str | None,
    app_name: str | None,
    show_status: bool,
    retry_group: str | None,
    dry_run: bool,
) -> None:
    r"""Upgrade control-plane charm groups.

    Upgrades K8s control-plane charms in dependency groups defined by
    release metadata. Groups are upgraded in order with pre/post-upgrade
    actions per group.

    \b
    Flags:
      --auto              Upgrade all remaining groups in order
      --group <name>      Upgrade a single group
      --application <name> Upgrade a single application
      --status            Show per-group status
      --retry-group <name> Retry a failed/blocked group
      --dry-run           Show plan without executing

    --application and --group are mutually exclusive. --auto overrides
    both. When using --application, dependency ordering is the
    operator's responsibility — upgrading an app whose predecessor
    groups are not complete may leave the cluster in an inconsistent
    state. Use --status to check group completion before proceeding.
    """
    deployment: Deployment = ctx.obj
    client = deployment.get_client()

    coordinator = ReleaseUpgradeCoordinator(client, UpgradeLogger())
    coordinator.load_state()
    hop = coordinator.get_current_hop()
    if hop is None:
        raise click.ClickException("No active upgrade hop. Run preflight first.")

    metadata = load_upgrade_metadata(hop.to_release)
    handler = ControlPlaneHandler(deployment, to_release=hop.to_release)
    control_plane_state = hop.phases.control_plane

    if show_status:
        _print_control_plane_status(hop, metadata, control_plane_state)
        return

    if dry_run:
        _print_dry_run(
            hop, metadata, control_plane_state, handler, group_name=group_name
        )
        return

    _validate_flags(auto, group_name, app_name, retry_group, show_status, dry_run)

    charm_name = None
    if app_name:
        charm_name = _resolve_charm_name(deployment, app_name, metadata)
        _check_application_group(charm_name, metadata, control_plane_state)

    _execute_control_plane(
        coordinator,
        handler,
        metadata,
        auto,
        group_name,
        charm_name,
        retry_group,
        control_plane_state,
    )
