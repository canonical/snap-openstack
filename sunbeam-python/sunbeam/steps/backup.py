# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Steps and component registry for ``sunbeam backup``.

Backup is organized around a pluggable :class:`BackupComponent` registry so that
discovery, target resolution, concurrent dispatch, and manifest writing stay
component-agnostic.
"""

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from snaphelpers import Snap

from sunbeam.core.common import SHARE_PATH, BaseStep, Result, ResultType, StepContext
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    LeaderNotFoundException,
    ModelNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL

LOG = logging.getLogger(__name__)

MYSQL_CHARM = "mysql-k8s"
VAULT_CHARM = "vault-k8s"
BACKUP_ACTION = "create-backup"
CLUSTER_STATUS_ACTION = "get-cluster-status"
DEFAULT_BACKUP_TIMEOUT = 300
BACKUP_MANIFEST_DIR = SHARE_PATH / "backups"
VAULT_PREREQUISITE_MSG = (
    "Vault backup/restore requires the unseal keys and root token that were in effect "
    "when the backup was created. Are you sure you want to continue?"
)


@dataclass
class BackupTarget:
    """An application and the unit chosen to run its backup action against."""

    app: str
    unit: str
    component: str
    action: str
    scale: int
    is_replica: bool = False


@dataclass
class BackupResult:
    """The outcome of attempting a backup for a single application."""

    app: str
    unit: str
    component: str
    success: bool
    backup_id: str | None = None
    error: str | None = None


@dataclass
class BackupComponent:
    """Descriptor for a kind of stateful application that can be backed up."""

    name: str
    charm_names: list[str]
    action: str
    resolve_target: Callable[[JujuHelper, str, str, bool], BackupTarget | None]
    backup_id_key: str = "backup-id"
    supports_force: bool = False


def _secondary_unit_from_status(units: list[str], action_result: dict) -> str | None:
    """Map a SECONDARY cluster member to a Juju unit name.

    The cluster status reports members by address/label rather than Juju unit
    name, so match members flagged SECONDARY back to one of the application's
    Juju units by ordinal.
    """
    try:
        status = json.loads(action_result.get("status", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None

    topology = status.get("defaultreplicaset", {}).get("topology", {})
    secondary_labels = [
        label
        for label, info in topology.items()
        if isinstance(info, dict) and info.get("memberrole", "").upper() == "SECONDARY"
    ]
    if not secondary_labels:
        return None

    for label in secondary_labels:
        ordinal = label.split(".")[0].rsplit("-", 1)[-1]
        for unit in units:
            if unit.rsplit("/", 1)[-1] == ordinal:
                return unit
    return None


def _resolve_mysql_target(
    jhelper: JujuHelper, app: str, model: str, force: bool
) -> BackupTarget | None:
    """Resolve a MySQL backup target, preferring a secondary (replica) unit.

    Falls back to the leader when no secondary is available. When cluster status
    cannot be read, the application is skipped unless ``force`` is set, in which
    case the leader is used as a best-effort target.
    """
    try:
        leader = jhelper.get_leader_unit(app, model)
        units = list(jhelper.get_application(app, model).units)
    except (LeaderNotFoundException, ApplicationNotFoundException):
        LOG.warning("Could not resolve %s, skipping", app)
        return None

    scale = len(units)
    try:
        result = jhelper.run_action(leader, model, CLUSTER_STATUS_ACTION)
        secondary = _secondary_unit_from_status(units, result)
        if secondary is not None:
            return BackupTarget(
                app=app,
                unit=secondary,
                component="mysql",
                action=BACKUP_ACTION,
                scale=scale,
                is_replica=True,
            )
    except ActionFailedException as e:
        if not force:
            LOG.warning("Could not resolve backup target for %s, skipping: %s", app, e)
            return None
        LOG.warning(
            "Could not resolve backup target for %s, using leader (--force): %s",
            app,
            e,
        )

    return BackupTarget(
        app=app,
        unit=leader,
        component="mysql",
        action=BACKUP_ACTION,
        scale=scale,
        is_replica=False,
    )


def _resolve_vault_target(
    jhelper: JujuHelper, app: str, model: str, force: bool
) -> BackupTarget | None:
    """Resolve the Vault backup target to the leader unit."""
    try:
        leader = jhelper.get_leader_unit(app, model)
        units = list(jhelper.get_application(app, model).units)
    except (LeaderNotFoundException, ApplicationNotFoundException):
        LOG.warning("Could not resolve %s, skipping", app)
        return None

    return BackupTarget(
        app=app,
        unit=leader,
        component="vault",
        action=BACKUP_ACTION,
        scale=len(units),
        is_replica=False,
    )


BACKUP_COMPONENTS: list[BackupComponent] = [
    BackupComponent(
        name="mysql",
        charm_names=[MYSQL_CHARM],
        action=BACKUP_ACTION,
        resolve_target=_resolve_mysql_target,
        supports_force=True,
    ),
    BackupComponent(
        name="vault",
        charm_names=[VAULT_CHARM],
        action=BACKUP_ACTION,
        resolve_target=_resolve_vault_target,
    ),
]


def _component_for(name: str) -> BackupComponent | None:
    return next((c for c in BACKUP_COMPONENTS if c.name == name), None)


class DiscoverBackupApplicationsStep(BaseStep):
    """Discover applications of every registered backup component in the model."""

    def __init__(
        self,
        jhelper: JujuHelper,
        model: str = OPENSTACK_MODEL,
        components: list[BackupComponent] = BACKUP_COMPONENTS,
    ):
        super().__init__(
            "Discover backup applications",
            "Discovering stateful applications to back up",
        )
        self.jhelper = jhelper
        self.components = components
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Return a mapping of component name to discovered application names."""
        try:
            status = self.jhelper.get_model_status(self.model)
        except (ModelNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        discovered: dict[str, list[str]] = {c.name: [] for c in self.components}
        for app_name, app_status in status.apps.items():
            charm_name = app_status.charm_name or ""
            for component in self.components:
                if charm_name in component.charm_names:
                    discovered[component.name].append(app_name)

        return Result(ResultType.COMPLETED, discovered)


class ResolveBackupTargetsStep(BaseStep):
    """Resolve the unit to back up for every discovered application."""

    def __init__(
        self,
        jhelper: JujuHelper,
        discovered: dict[str, list[str]],
        model: str = OPENSTACK_MODEL,
        force: bool = False,
    ):
        super().__init__(
            "Resolve backup targets",
            "Resolving units for consistent backup targets",
        )
        self.jhelper = jhelper
        self.discovered = discovered
        self.force = force
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Return a flat list of resolved backup targets across components."""
        targets: list[BackupTarget] = []
        for component_name, apps in self.discovered.items():
            component = _component_for(component_name)
            if component is None:
                continue
            for app in apps:
                target = component.resolve_target(
                    self.jhelper, app, self.model, self.force
                )
                if target is None:
                    self.update_status(context, f"skipped {app}")
                    continue
                targets.append(target)

        return Result(ResultType.COMPLETED, targets)


def _run_single_backup(
    jhelper: JujuHelper,
    target: BackupTarget,
    backup_id_key: str,
    model: str,
    timeout: int,
    action_params: dict | None = None,
) -> BackupResult:
    """Dispatch a single backup action and capture the result.

    Never raises: any failure is encoded in the returned :class:`BackupResult`.
    """
    try:
        result = jhelper.run_action(
            target.unit, model, target.action, action_params, timeout=timeout
        )
        return BackupResult(
            app=target.app,
            unit=target.unit,
            component=target.component,
            success=True,
            backup_id=result.get(backup_id_key),
        )
    except Exception as e:
        return BackupResult(
            app=target.app,
            unit=target.unit,
            component=target.component,
            success=False,
            error=str(e),
        )


class RunBackupsStep(BaseStep):
    """Dispatch every target's backup action concurrently and collect results."""

    def __init__(
        self,
        jhelper: JujuHelper,
        targets: list[BackupTarget],
        force: bool = False,
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Run backups", "Dispatching backups across applications")
        self.jhelper = jhelper
        self.targets = targets
        self.force = force
        self.timeout = timeout
        self.model = model

    def is_skip(self, context: StepContext) -> Result:
        """Skip when there are no resolved targets to back up."""
        if not self.targets:
            return Result(ResultType.SKIPPED, "No backup targets resolved")
        return Result(ResultType.COMPLETED)

    def _action_params(self, component: BackupComponent | None) -> dict | None:
        if self.force and component is not None and component.supports_force:
            return {"force": True}
        return None

    def run(self, context: StepContext) -> Result:
        """Dispatch backups concurrently, returning a list of results."""
        components = {
            target.component: _component_for(target.component)
            for target in self.targets
        }
        results: list[BackupResult] = []
        with ThreadPoolExecutor(max_workers=len(self.targets)) as executor:
            futures = []
            for target in self.targets:
                component = components[target.component]
                backup_id_key = component.backup_id_key if component else "backup-id"
                futures.append(
                    executor.submit(
                        _run_single_backup,
                        self.jhelper,
                        target,
                        backup_id_key,
                        self.model,
                        self.timeout,
                        self._action_params(component),
                    )
                )
            for future in as_completed(futures):
                results.append(future.result())

        return Result(ResultType.COMPLETED, results)


class WriteBackupManifestStep(BaseStep):
    """Write a timestamped manifest of the backup run to the snap share path."""

    def __init__(
        self,
        results: list[BackupResult],
        dispatched_at: str,
        manifest_dir: Path | None = None,
    ):
        super().__init__("Write backup manifest", "Writing backup manifest")
        self.results = results
        self.dispatched_at = dispatched_at
        self.manifest_dir = manifest_dir

    def run(self, context: StepContext) -> Result:
        """Write the manifest and return its path."""
        if self.manifest_dir is not None:
            directory = self.manifest_dir
        else:
            directory = Snap().paths.user_common / BACKUP_MANIFEST_DIR
        directory.mkdir(parents=True, exist_ok=True)

        succeeded = sum(1 for r in self.results if r.success)
        failed = len(self.results) - succeeded
        manifest = {
            "dispatched_at": self.dispatched_at,
            "summary": {"succeeded": succeeded, "failed": failed},
            "results": [asdict(r) for r in self.results],
        }

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = directory / f"backup-manifest-{timestamp}.yaml"
        try:
            with path.open("w") as manifest_file:
                yaml.safe_dump(manifest, manifest_file, sort_keys=False)
        except OSError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED, str(path))
