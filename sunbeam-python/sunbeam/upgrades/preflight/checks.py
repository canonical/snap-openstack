# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Health-check framework for the upgrade preflight phase.

Each check subclasses ``sunbeam.core.checks.Check`` and sets
``self.exit_code`` to 1 (operational failure) or 2 (invalid hop /
unsupported / missing metadata) per the upgrade exit-code table. The
runner ``run_upgrade_preflight_checks`` short-circuits on the first
failure and raises ``click.ClickException`` carrying the exit code.

Checks are ordered: cheap static checks (snap version, metadata/hop
validity) run before expensive Juju/MySQL checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import click
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.core.checks import Check
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.upgrades.metadata import HopMetadata, load_upgrade_metadata
from sunbeam.versions import (
    detect_snap_release,
    is_valid_hop,
)

LOG = logging.getLogger(__name__)

# The K8s control-plane model is always "openstack" (see
# sunbeam.core.openstack.OPENSTACK_MODEL). The machines model name varies
# by provider — use deployment.openstack_machines_model.

# Tolerated ``blocked`` workload messages. A charm in ``blocked`` with a
# message in this set passes preflight; anything else fails. Each entry
# has a comment naming the charm that emits it. Start empty and grow as
# integration surfaces known-safe blocks.
TOLERATED_BLOCKED_MESSAGES: set[str] = set(
    {
        # e.g. "Manual security enable required"  # sunbeam-machine
    }
)


@dataclass
class CheckContext:
    """Shared state for preflight checks.

    Carries everything the checks need so each check is a pure function
    of (ctx) -> bool. Constructed once by the CLI command and passed to
    every check. Client, JujuHelper, and model names all come from the
    deployment.
    """

    deployment: Deployment
    from_release: str
    to_release: str
    snap: Snap | None = None
    metadata: HopMetadata | None = None

    @property
    def client(self) -> Client:
        """Return the clusterd client for this deployment."""
        return self.deployment.get_client()

    @property
    def jhelper(self) -> JujuHelper:
        """Return a JujuHelper for this deployment."""
        return self.deployment.get_juju_helper()

    @property
    def machines_model(self) -> str:
        """Return the machines model name for this deployment."""
        return self.deployment.openstack_machines_model


def run_upgrade_preflight_checks(
    checks: Sequence[Check],
    console: Console,
) -> None:
    """Run preflight checks sequentially.

    Like ``sunbeam.core.checks.run_preflight_checks`` but respects the
    upgrade exit-code distinction: each failed check carries an
    ``exit_code`` (1 = operational, 2 = invalid hop / metadata).
    Short-circuits on the first failure.
    """
    for check in checks:
        LOG.debug("Starting preflight check %s", check.name)
        with console.status(f"{check.description} ... "):
            passed = check.run()
        if passed:
            click.echo(f"  ✓ {check.name}")
        else:
            exit_code = getattr(check, "exit_code", 1)
            click.echo(f"  ✗ {check.name}: {check.message}")
            raise click.ClickException(f"[exit {exit_code}] {check.message}")


class UpgradeCheck(Check):
    """Base class for upgrade preflight checks.

    Adds ``exit_code`` (1 or 2) on top of ``Check``.
    """

    def __init__(self, name: str, description: str = "", exit_code: int = 1):
        super().__init__(name, description)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Static checks (no Juju, no MySQL — snap / metadata / hop validity)
# ---------------------------------------------------------------------------


class SnapVersionCheck(UpgradeCheck):
    """Snap release matches the target release.

    The snap's ``deployment.version`` config must equal the target
    release. The operator refreshes the snap to the target before
    running preflight; this catches a stale snap.
    """

    def __init__(self, ctx: CheckContext):
        super().__init__(
            "Check snap version matches target",
            "Checking snap version matches target release",
            exit_code=2,
        )
        self.ctx = ctx

    def run(self, check_status: Status | None = None) -> bool:
        """Return False if snap release does not match the target."""
        snap_release = detect_snap_release()
        if snap_release != self.ctx.to_release:
            self.message = (
                f"Snap is at release {snap_release!r} but target is "
                f"{self.ctx.to_release!r}. Refresh the snap to the "
                "target release before running upgrade."
            )
            return False
        return True


class HopMetadataCheck(UpgradeCheck):
    """Validate the upgrade hop: metadata present, compatible, and hop is valid.

    Combines three sub-checks into one:
    - Metadata file exists and loads for the target release.
    - Metadata ``from``/``to`` match the requested hop.
    - The from->to pair is a supported upgrade path.

    Failing any sub-check fails the check with exit code 2.
    """

    def __init__(self, ctx: CheckContext):
        super().__init__(
            "Check upgrade hop and metadata",
            "Checking upgrade hop validity and metadata",
            exit_code=2,
        )
        self.ctx = ctx

    def run(self, check_status: Status | None = None) -> bool:
        """Return False if hop is invalid or metadata is missing/incompatible."""
        if self.ctx.from_release == self.ctx.to_release:
            self.message = (
                f"Source and target releases are both "
                f"{self.ctx.to_release!r}. The snap must be refreshed to the "
                "target release before running the upgrade: "
                "'sudo snap refresh openstack --channel=<target>/stable'."
            )
            return False
        if not is_valid_hop(self.ctx.from_release, self.ctx.to_release):
            self.message = (
                f"Hop {self.ctx.from_release} -> {self.ctx.to_release} is "
                "not a supported upgrade path."
            )
            return False
        try:
            self.ctx.metadata = load_upgrade_metadata(self.ctx.to_release)
        except FileNotFoundError as e:
            self.message = str(e)
            return False
        except Exception as e:
            self.message = f"Failed to load upgrade metadata: {e}"
            return False
        if self.ctx.metadata.from_release != self.ctx.from_release:
            self.message = (
                f"Metadata from_release {self.ctx.metadata.from_release!r} "
                f"does not match requested from {self.ctx.from_release!r}"
            )
            return False
        if self.ctx.metadata.to_release != self.ctx.to_release:
            self.message = (
                f"Metadata to_release {self.ctx.metadata.to_release!r} "
                f"does not match requested to {self.ctx.to_release!r}"
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Juju checks (model status)
# ---------------------------------------------------------------------------


class ClusterHealthCheck(UpgradeCheck):
    """All Juju models are healthy.

    Iterates both the control-plane model (``openstack``) and the
    machines model (provider-specific). Every app must be ``active``
    or ``idle``. ``blocked`` passes only if the workload message is
    in ``TOLERATED_BLOCKED_MESSAGES``. Anything else fails.
    """

    def __init__(self, ctx: CheckContext):
        super().__init__(
            "Check Juju cluster health",
            "Checking Juju cluster health",
            exit_code=1,
        )
        self.ctx = ctx

    def run(self, check_status: Status | None = None) -> bool:
        """Return False if any app in any model is not healthy."""
        unhealthy: list[str] = []
        for model in (OPENSTACK_MODEL, self.ctx.machines_model):
            try:
                status = self.ctx.jhelper.get_model_status(model)
            except Exception as e:
                unhealthy.append(f"{model}: unreachable ({e})")
                continue
            for app_name, app in status.apps.items():
                current = app.app_status.current
                if current in ("active", "idle"):
                    continue
                if current == "blocked":
                    msg = app.app_status.message or ""
                    if msg in TOLERATED_BLOCKED_MESSAGES:
                        continue
                unhealthy.append(
                    f"{model}/{app_name}: {current}"
                    + (f" ({app.app_status.message})" if app.app_status.message else "")
                )
        if unhealthy:
            self.message = (
                "Juju cluster is not healthy:\n  "
                + "\n  ".join(unhealthy)
                + "\nResolve these issues before retrying the upgrade."
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Database checks
# ---------------------------------------------------------------------------


class MySQLQuorumCheck(UpgradeCheck):
    """MySQL has quorum.

    Runs the ``get-cluster-status`` action on the ``mysql-k8s`` leader
    unit. The action returns the cluster topology; if it succeeds and
    reports a healthy cluster, quorum is up. If no leader is found or
    the action fails, quorum may be lost.
    """

    APP = "mysql"
    MODEL = OPENSTACK_MODEL

    def __init__(self, ctx: CheckContext):
        super().__init__(
            "Check MySQL quorum",
            "Checking MySQL quorum (cluster status)",
            exit_code=1,
        )
        self.ctx = ctx

    def run(self, check_status: Status | None = None) -> bool:
        """Return False if MySQL cluster status action fails."""
        try:
            leader = self.ctx.jhelper.get_leader_unit(self.APP, self.MODEL)
        except Exception as e:
            self.message = f"Cannot find MySQL leader: {e}"
            return False
        if not leader:
            self.message = (
                "MySQL has no leader unit — quorum may be lost. "
                "Check mysql-k8s unit status before retrying."
            )
            return False
        try:
            result = self.ctx.jhelper.run_action(
                leader, self.MODEL, "get-cluster-status"
            )
        except Exception as e:
            self.message = f"MySQL get-cluster-status action failed: {e}"
            return False
        if not result:
            self.message = (
                "MySQL get-cluster-status returned no result. "
                "Check mysql-k8s unit status before retrying."
            )
            return False
        return True


def build_preflight_checks(
    ctx: CheckContext, capacity_override: bool = False
) -> list[Check]:
    """Construct the ordered list of preflight checks.

    Order matters: cheap static checks run first (fail fast on a
    stale snap or invalid hop before touching Juju/MySQL).

    :param capacity_override: skip the capacity check (for
        --capacity-policy-override CLI flag).
    """
    from sunbeam.upgrades.preflight.capacity import CapacityCheck

    return [
        SnapVersionCheck(ctx),
        HopMetadataCheck(ctx),
        ClusterHealthCheck(ctx),
        CapacityCheck(ctx, override=capacity_override),
        MySQLQuorumCheck(ctx),
    ]
