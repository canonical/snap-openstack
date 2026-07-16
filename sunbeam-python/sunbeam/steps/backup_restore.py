# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Steps and component registry for ``sunbeam backup/restore``."""

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
    JujuWaitException,
    LeaderNotFoundException,
    ModelNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL

LOG = logging.getLogger(__name__)

MYSQL_CHARM = "mysql-k8s"
VAULT_CHARM = "vault-k8s"
BACKUP_ACTION = "create-backup"
LIST_BACKUPS_ACTION = "list-backups"
CLUSTER_STATUS_ACTION = "get-cluster-status"
MYSQL_S3_RELATION = "s3-parameters"
VAULT_S3_RELATION = "s3-parameters"
S3_INTERFACE = "s3"
DEFAULT_BACKUP_TIMEOUT = 300
BACKUP_MANIFEST_DIR = SHARE_PATH / "backups"
DEFAULT_SCALE_TIMEOUT = 1800
DEFAULT_RESTORE_TIMEOUT = 1800
MYSQL_RESTORE_ACTION = "restore"
VAULT_RESTORE_ACTION = "restore-backup"
PAUSE_ACTION = "pause"
RESUME_ACTION = "resume"
RESTORE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


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
class BackupItem:
    """A single backup entry in a backup inventory."""

    backup_id: str
    success: bool | None = None


@dataclass
class BackupResult:
    """The outcome of attempting a backup for a single application."""

    app: str
    unit: str
    component: str
    backup: BackupItem | None = None
    error: str | None = None


@dataclass
class BackupInventory:
    """The result of listing backup IDs for a single application target."""

    app: str
    unit: str
    component: str
    backups: list[BackupItem] | None = None
    error: str | None = None


@dataclass
class BackupComponent:
    """Descriptor for a kind of stateful application that can be backed up."""

    name: str
    charm_names: list[str]
    action: str
    resolve_target: Callable[[JujuHelper, str, str, bool], BackupTarget | None]
    list_action: str = LIST_BACKUPS_ACTION
    parse_backups: Callable[[dict], list[BackupItem]] | None = None
    backup_id_key: str = "backup-id"
    supports_force: bool = False


def _component_for(name: str) -> BackupComponent | None:
    return next((c for c in BACKUP_COMPONENTS if c.name == name), None)


def _is_app_active(app_status: object) -> bool:
    """Return whether application workload status is active."""
    status = getattr(app_status, "app_status", None)
    current = getattr(status, "current", None)
    if not isinstance(current, str):
        return True
    return current == "active"


def _app_status_value(app_status: object) -> str:
    """Return application workload status string for display/logging."""
    status = getattr(app_status, "app_status", None)
    current = getattr(status, "current", None)
    if isinstance(current, str):
        return current
    return "unknown"


def _parse_mysql_backups(action_result: dict) -> list[BackupItem]:
    """Parse MySQL list-backups output table and return finished backup IDs."""
    backups_text = action_result.get("backups")
    if backups_text is None:
        backups_text = (action_result.get("results") or {}).get("backups")
    if not isinstance(backups_text, str):
        return []

    backups: list[BackupItem] = []
    for raw_line in backups_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("backup-id") or set(line) == {"-"}:
            continue
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 3:
            continue
        if columns[2].lower() != "finished":
            backups.append(BackupItem(backup_id=columns[0], success=False))
            continue
        backups.append(BackupItem(columns[0], success=True))
    return backups


def _parse_vault_backups(action_result: dict) -> list[BackupItem]:
    """Parse Vault list-backups output and return backup IDs."""
    raw_backup_ids = action_result.get("backup-ids")
    if raw_backup_ids is None or not isinstance(raw_backup_ids, str):
        return []

    try:
        parsed = json.loads(raw_backup_ids)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [BackupItem(backup_id=str(backup_id), success=True) for backup_id in parsed]


def _secondary_unit_from_status(units: list[str], action_result: dict) -> str | None:
    """Map a SECONDARY cluster member to a Juju unit name.

    The cluster status reports members by address/label rather than Juju unit
    name, so match members flagged SECONDARY back to one of the application's
    Juju units by ordinal.
    """
    status = action_result.get("status") or {}
    topology = status.get("defaultreplicaset", {}).get("topology", {})
    secondary_labels = [
        label
        for label, info in topology.items()
        if isinstance(info, dict) and info.get("memberrole", "").lower() == "secondary"
    ]
    if not secondary_labels:
        return None

    for label in secondary_labels:
        ordinal = label.split(".")[0].rsplit("-", 1)[-1]
        for unit in units:
            if unit.rsplit("/", 1)[-1] == ordinal:
                return unit
    return None


def _latest_backup(backups: list[BackupItem]) -> str | None:
    """Return the lexicographically latest successful backup ID, if present."""
    successful = [b for b in backups if b.success]
    if not successful:
        return None
    return sorted(successful, key=lambda b: b.backup_id)[-1].backup_id


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


def _run_single_backup(
    jhelper: JujuHelper,
    target: BackupTarget,
    backup_id_key: str,
    model: str,
    timeout: int,
    action_params: dict | None = None,
) -> BackupResult:
    """Dispatch a single backup action and capture the result.

    Any failure is encoded in the returned :class:`BackupResult`.
    """
    try:
        result = jhelper.run_action(
            target.unit, model, target.action, action_params, timeout=timeout
        )
        backup_id = result.get(backup_id_key)
        return BackupResult(
            app=target.app,
            unit=target.unit,
            component=target.component,
            backup=BackupItem(backup_id=backup_id, success=True)
            if backup_id is not None
            else None,
        )
    except Exception as e:
        return BackupResult(
            app=target.app,
            unit=target.unit,
            component=target.component,
            error=str(e),
        )


def _run_single_list_backups(
    jhelper: JujuHelper,
    target: BackupTarget,
    action_name: str,
    parse_backups: Callable[[dict], list[BackupItem]] | None,
    model: str,
    timeout: int,
) -> BackupInventory:
    """Dispatch a single list-backups action and parse backup IDs."""
    try:
        result = jhelper.run_action(target.unit, model, action_name, timeout=timeout)
        backups = parse_backups(result) if parse_backups is not None else []
        return BackupInventory(
            app=target.app,
            unit=target.unit,
            component=target.component,
            backups=backups,
        )
    except Exception as e:
        return BackupInventory(
            app=target.app,
            unit=target.unit,
            component=target.component,
            error=str(e),
        )


BACKUP_COMPONENTS: list[BackupComponent] = [
    BackupComponent(
        name="mysql",
        charm_names=[MYSQL_CHARM],
        action=BACKUP_ACTION,
        resolve_target=_resolve_mysql_target,
        parse_backups=_parse_mysql_backups,
        supports_force=True,
    ),
    BackupComponent(
        name="vault",
        charm_names=[VAULT_CHARM],
        action=BACKUP_ACTION,
        resolve_target=_resolve_vault_target,
        parse_backups=_parse_vault_backups,
    ),
]


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
        non_active_targets: list[tuple[str, str]] = []
        for app_name, app_status in status.apps.items():
            charm_name = app_status.charm_name or ""
            for component in self.components:
                if charm_name in component.charm_names:
                    if not _is_app_active(app_status):
                        non_active_targets.append(
                            (app_name, _app_status_value(app_status))
                        )
                        break
                    discovered[component.name].append(app_name)

        if non_active_targets:
            details = ", ".join(
                f"{app}({state})" for app, state in sorted(non_active_targets)
            )
            return Result(
                ResultType.FAILED,
                "Target applications must be active before backup/restore operations: "
                + details,
            )

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
            "Resolving units for backup targets",
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


class CheckS3RelationsStep(BaseStep):
    """Partition applications by required relation name and S3 interface."""

    def __init__(
        self,
        jhelper: JujuHelper,
        applications: list[str],
        endpoint_name: str,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            "Check S3 relations",
            "Checking applications related to S3",
        )
        self.jhelper = jhelper
        self.applications = applications
        self.endpoint_name = endpoint_name
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Return related/unrelated applications by relation name + interface."""
        try:
            status = self.jhelper.get_model_status(self.model)
        except (ModelNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        related: list[str] = []
        unrelated: list[str] = []
        for app_name in self.applications:
            app_status = status.apps.get(app_name)
            if app_status is None:
                unrelated.append(app_name)
                continue
            relations = app_status.relations or {}
            endpoint_relations = relations.get(self.endpoint_name, [])
            if any(rel.interface == S3_INTERFACE for rel in endpoint_relations):
                related.append(app_name)
            else:
                unrelated.append(app_name)

        return Result(
            ResultType.COMPLETED,
            {
                "related": sorted(related),
                "unrelated": sorted(unrelated),
            },
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


class ListBackupsStep(BaseStep):
    """Dispatch list-backups actions concurrently and collect inventories."""

    def __init__(
        self,
        jhelper: JujuHelper,
        targets: list[BackupTarget],
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("List backups", "Listing backups across applications")
        self.jhelper = jhelper
        self.targets = targets
        self.timeout = timeout
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Dispatch list-backups concurrently, returning inventory entries."""
        if not self.targets:
            return Result(ResultType.COMPLETED, [])

        components = {
            target.component: _component_for(target.component)
            for target in self.targets
        }
        inventories: list[BackupInventory] = []
        with ThreadPoolExecutor(max_workers=len(self.targets)) as executor:
            futures = []
            for target in self.targets:
                component = components[target.component]
                action_name = (
                    component.list_action
                    if component is not None
                    else LIST_BACKUPS_ACTION
                )
                parser = component.parse_backups if component is not None else None
                futures.append(
                    executor.submit(
                        _run_single_list_backups,
                        self.jhelper,
                        target,
                        action_name,
                        parser,
                        self.model,
                        self.timeout,
                    )
                )
            for future in as_completed(futures):
                inventories.append(future.result())

        return Result(ResultType.COMPLETED, inventories)


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

        succeeded = sum(
            1 for r in self.results if r.backup is not None and r.backup.success
        )
        failed = sum(1 for r in self.results if r.error is not None)
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


class WriteBackupInventoryManifestStep(BaseStep):
    """Write a timestamped manifest for backup inventory results."""

    def __init__(
        self,
        results: list[BackupInventory],
        listed_at: str,
        manifest_dir: Path | None = None,
    ):
        super().__init__("Write backup inventory manifest", "Writing backup inventory")
        self.results = results
        self.listed_at = listed_at
        self.manifest_dir = manifest_dir

    def run(self, context: StepContext) -> Result:
        """Write the inventory manifest and return its path."""
        if self.manifest_dir is not None:
            directory = self.manifest_dir
        else:
            directory = Snap().paths.user_common / BACKUP_MANIFEST_DIR
        directory.mkdir(parents=True, exist_ok=True)

        succeeded = sum(
            1 for r in self.results if r.backups and any(b.success for b in r.backups)
        )
        failed = len(self.results) - succeeded
        manifest = {
            "listed_at": self.listed_at,
            "summary": {"succeeded": succeeded, "failed": failed},
            "results": [asdict(r) for r in self.results],
        }

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = directory / f"backup-inventory-{timestamp}.yaml"
        try:
            with path.open("w") as manifest_file:
                yaml.safe_dump(manifest, manifest_file, sort_keys=False)
        except OSError as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED, str(path))


class _ApiAppActionStep(BaseStep):
    """Dispatch an action on an application's leader."""

    def __init__(
        self,
        jhelper: JujuHelper,
        name: str,
        description: str,
        app: str,
        action_name: str,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(name, description)
        self.jhelper = jhelper
        self.model = model
        self.app = app
        self.action_name = action_name

    def run(self, context: StepContext) -> Result:
        """Run action on the application's leader."""
        try:
            leader = self.jhelper.get_leader_unit(self.app, self.model)
            self.jhelper.run_action(
                leader,
                self.model,
                self.action_name,
                timeout=DEFAULT_RESTORE_TIMEOUT,
            )
        except (
            ActionFailedException,
            LeaderNotFoundException,
            ModelNotFoundException,
            JujuException,
        ) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class PauseAppStep(_ApiAppActionStep):
    """Pause application API services."""

    def __init__(self, jhelper: JujuHelper, app: str, model: str = OPENSTACK_MODEL):
        super().__init__(
            jhelper,
            "Pause App",
            "Pause API container services",
            app,
            PAUSE_ACTION,
            model,
        )


class ResumeAppStep(_ApiAppActionStep):
    """Resume application API services."""

    def __init__(self, jhelper: JujuHelper, app: str, model: str = OPENSTACK_MODEL):
        super().__init__(
            jhelper,
            "Resume App",
            "Resume API container services",
            app,
            RESUME_ACTION,
            model,
        )


class ScaleAppStep(BaseStep):
    """Scale an application to a target number of units."""

    def __init__(
        self,
        jhelper: JujuHelper,
        application: str,
        scale: int,
        timeout: int = DEFAULT_SCALE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ) -> None:
        super().__init__("Scale App", f"Scaling {application} to {scale} unit(s)")
        self.jhelper = jhelper
        self.application = application
        self.scale = scale
        self.timeout = timeout
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Scale the application and wait for it to settle."""
        try:
            self.jhelper.scale_application(self.model, self.application, self.scale)
            self.jhelper.wait_until_active(
                self.model, apps=[self.application], timeout=self.timeout
            )
        except (JujuException, JujuWaitException, TimeoutError) as e:
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class CheckAppPauseResumeSupportStep(BaseStep):
    """Validate pause/resume action support for all target applications."""

    def __init__(
        self,
        jhelper: JujuHelper,
        apps: list[str],
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            "Check pause/resume support",
            "Validating pause/resume support before restore",
        )
        self.jhelper = jhelper
        self.apps = apps
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Fail fast if any app does not support pause/resume actions."""
        unsupported_apps: list[str] = []
        for app in self.apps:
            try:
                actions = self.jhelper.get_application_actions(app, self.model)
            except (JujuException, ModelNotFoundException):
                return Result(
                    ResultType.FAILED,
                    f"Unable to query actions for {app}. No changes have been made.",
                )

            if PAUSE_ACTION not in actions or RESUME_ACTION not in actions:
                unsupported_apps.append(app)

        if unsupported_apps:
            details = ", ".join(sorted(unsupported_apps))
            return Result(
                ResultType.FAILED,
                "The following applications do not support the 'pause/resume' "
                f"action required for restore: {details}. No changes have been made.",
            )

        return Result(ResultType.COMPLETED)


class RestoreMySQLStep(BaseStep):
    """Restore a MySQL application from a backup."""

    def __init__(
        self,
        jhelper: JujuHelper,
        target: BackupTarget,
        restore_to_time: str | None = None,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore MySQL", f"Restoring {target.app}")
        self.jhelper = jhelper
        self.target = target
        self.restore_to_time = restore_to_time
        self.model = model
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Restore MySQL using latest backup or restore-to-time."""
        try:
            leader = self.jhelper.get_leader_unit(self.target.app, self.model)
        except (LeaderNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        params: dict[str, str] = {}
        if self.restore_to_time is not None:
            params["restore-to-time"] = self.restore_to_time
        else:
            try:
                list_result = self.jhelper.run_action(
                    leader,
                    self.model,
                    LIST_BACKUPS_ACTION,
                    timeout=self.timeout,
                )
            except (ActionFailedException, JujuException) as e:
                return Result(ResultType.FAILED, str(e))

            latest = _latest_backup(_parse_mysql_backups(list_result))
            if latest is None:
                return Result(
                    ResultType.FAILED,
                    f"No finished MySQL backups found for {self.target.app}.",
                )
            params["backup-id"] = latest

        try:
            self.jhelper.run_action(
                leader,
                self.model,
                MYSQL_RESTORE_ACTION,
                params,
                timeout=self.timeout,
            )
        except (ActionFailedException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class RestoreVaultStep(BaseStep):
    """Restore a Vault application from a backup."""

    def __init__(
        self,
        jhelper: JujuHelper,
        target: BackupTarget,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore Vault", f"Restoring {target.app}")
        self.jhelper = jhelper
        self.target = target
        self.model = model
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Restore Vault from the latest available backup."""
        try:
            leader = self.jhelper.get_leader_unit(self.target.app, self.model)
        except (LeaderNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        try:
            list_result = self.jhelper.run_action(
                leader,
                self.model,
                LIST_BACKUPS_ACTION,
                timeout=self.timeout,
            )
        except (ActionFailedException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        latest = _latest_backup(_parse_vault_backups(list_result))
        if latest is None:
            return Result(
                ResultType.FAILED,
                f"No Vault backups found for {self.target.app}.",
            )

        try:
            self.jhelper.run_action(
                leader,
                self.model,
                VAULT_RESTORE_ACTION,
                {"backup-id": latest},
                timeout=self.timeout,
            )
        except (ActionFailedException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
