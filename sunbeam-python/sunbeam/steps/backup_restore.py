# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Steps and component registry for ``sunbeam backup/restore``.

The module composes and
runs the top-level steps defined here. All per-component logic (validation
checks, target resolution, and backup/list/restore/revert plans) lives on the
:class:`BackupComponent` registry and is driven by the wrapper steps below.
"""

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jubilant.statustypes import AppStatus
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

S3_INTEGRATOR_CHARM = "s3-integrator"
MYSQL_CHARM = "mysql-k8s"
VAULT_CHARM = "vault-k8s"
BACKUP_ACTION = "create-backup"
RESTORE_ACTION = "restore"
BACKUP_RESULT_ID_KEY = "backup-id"
LIST_BACKUPS_ACTION = "list-backups"
VAULT_RESTORE_ACTION = "restore-backup"
MYSQL_CLUSTER_STATUS_ACTION = "get-cluster-status"
S3_INTERFACE = "s3"
S3_ENDPOINT = "s3-parameters"
DEFAULT_BACKUP_TIMEOUT = 1800
DEFAULT_RESTORE_TIMEOUT = 1800
DEFAULT_ACTION_TIMEOUT = 120
BACKUP_MANIFEST_DIR = SHARE_PATH / "backups"
PAUSE_ACTION = "pause"
RESUME_ACTION = "resume"
RESTORE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class ActionTarget:
    """An application and the unit chosen to run an action against."""

    app: str
    unit: str
    component: str
    action: str


@dataclass
class BackupOutcome:
    """A backup entry in a backup inventory for an application."""

    backup_id: str
    success: bool | None = None


@dataclass
class BackupResult:
    """The outcome of attempting a backup for an application."""

    app: str
    unit: str
    component: str
    backup: BackupOutcome | None = None
    error: str | None = None


@dataclass
class BackupInventory:
    """The result of listing backup IDs for an application."""

    app: str
    unit: str
    component: str
    backups: list[BackupOutcome] | None = None
    error: str | None = None


@dataclass
class RestoreResult:
    """The outcome of attempting a restore for an application."""

    app: str
    component: str
    success: bool
    error: str | None = None
    reverted: bool = False
    rollback_error: str | None = None


@dataclass
class PreparedRestore:
    """A restore and revert plan."""

    component: "BackupComponent"
    target: ActionTarget
    plan: list[BaseStep]
    revert_plan: list[BaseStep]


@dataclass
class ValidationCheck:
    """An application-readiness check."""

    name: str
    predicate: Callable[[AppStatus], bool]
    forceable: bool = False


class BackupComponent(ABC):
    """Backup and restore workflow contract for an app."""

    name: str
    backup_action: str = BACKUP_ACTION
    restore_action: str = RESTORE_ACTION
    list_action: str = LIST_BACKUPS_ACTION
    backup_id_param: str = BACKUP_RESULT_ID_KEY
    restore_to_time_param: str | None = None

    @property
    def validate_checks(self) -> list[ValidationCheck]:
        """Return readiness checks applied before backup or restore."""
        return [APP_READY_VALIDATION_CHECK, S3_RELATION_VALIDATION_CHECK]

    @abstractmethod
    def resolve_backup_target(
        self, jhelper: JujuHelper, app: str, model: str, force: bool
    ) -> ActionTarget | None:
        """Resolve the unit on which to run a backup action."""

    @abstractmethod
    def parse_backup_list(self, action_result: dict) -> list[BackupOutcome]:
        """Parse a component-specific list-backups action result."""

    def parse_backup(self, action_result: dict) -> BackupOutcome | None:
        """Parse create-backup output and return the backup ID."""
        backup_id = action_result.get(BACKUP_RESULT_ID_KEY)
        if not isinstance(backup_id, str):
            return None
        return BackupOutcome(backup_id=backup_id, success=True)

    def build_backup_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Build the common single-action backup plan."""
        return [_BackupAppStep(jhelper, self, target, timeout=timeout, model=model)]

    def build_restore_precheck_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Return non-destructive checks that must pass before restore."""
        return []

    def latest_backup_params(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> dict[str, str]:
        """Resolve the latest successful backup into restore action parameters."""
        list_result = jhelper.run_action(
            target.unit,
            model,
            self.list_action,
            timeout=timeout,
        )
        latest = _latest_backup(self.parse_backup_list(list_result))
        if latest is None:
            raise JujuException(f"No finished backups found for {target.app}.")
        return {self.backup_id_param: latest}

    @abstractmethod
    def restore_params(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> dict[str, str]:
        """Build parameters that satisfy this component's restore action contract."""

    @abstractmethod
    def build_restore_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Build the operational restore sequence."""

    def build_restore_revert_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Build revert steps for a failed restore."""
        return []


def _component_for(name: str) -> BackupComponent | None:
    return next((c for c in BACKUP_COMPONENTS if c.name == name), None)


# ---------------------------------------------------------------------------
# Validation predicates
# ---------------------------------------------------------------------------
def _is_app_active(app_status: AppStatus) -> bool:
    """Return whether application workload status is active."""
    return app_status.app_status.current == "active"


def _is_related_to_s3(app_status: AppStatus) -> bool:
    """Return whether the application is related to S3 via the endpoint."""
    endpoint_relations = app_status.relations.get(S3_ENDPOINT, [])
    return any(rel.interface == S3_INTERFACE for rel in endpoint_relations)


APP_READY_VALIDATION_CHECK: ValidationCheck = ValidationCheck(
    name="active",
    predicate=_is_app_active,
    forceable=True,
)

S3_RELATION_VALIDATION_CHECK: ValidationCheck = ValidationCheck(
    name="s3-relation",
    predicate=_is_related_to_s3,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _secondary_unit_from_status(units: list[str], action_result: dict) -> str | None:
    """Map a SECONDARY mysql cluster member to a Juju unit name."""
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


def _latest_backup(backups: list[BackupOutcome]) -> str | None:
    """Return the lexicographically latest successful backup ID, if present."""
    successful = [b for b in backups if b.success]
    if not successful:
        return None
    return sorted(successful, key=lambda b: b.backup_id)[-1].backup_id


# ---------------------------------------------------------------------------
# Atomic action steps
# ---------------------------------------------------------------------------
class _ActionStep(BaseStep):
    """Dispatch an action on an application's leader or all units."""

    def __init__(
        self,
        jhelper: JujuHelper,
        name: str,
        description: str,
        app: str,
        action_name: str,
        run_on_all_units: bool = False,
        expected_status: list[str] | None = None,
        timeout: int = DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(name, description)
        self.jhelper = jhelper
        self.model = model
        self.app = app
        self.action_name = action_name
        self.run_on_all_units = run_on_all_units
        self.timeout = timeout
        self.expected_status = expected_status or ["active"]

    def run(self, context: StepContext) -> Result:
        """Run action on the application's leader or all units."""
        try:
            if self.run_on_all_units:
                units = list(self.jhelper.get_application(self.app, self.model).units)
            else:
                units = [self.jhelper.get_leader_unit(self.app, self.model)]

            for unit in units:
                self.jhelper.run_action(
                    unit,
                    self.model,
                    self.action_name,
                    timeout=self.timeout,
                )
            self.jhelper.wait_until_desired_status(
                self.model,
                apps=[self.app],
                status=self.expected_status,
                agent_status=["idle"],
                timeout=self.timeout,
            )
        except (
            ActionFailedException,
            LeaderNotFoundException,
            ModelNotFoundException,
            JujuException,
        ) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class _PauseAppStep(_ActionStep):
    """Pause application API services."""

    def __init__(
        self,
        jhelper: JujuHelper,
        app: str,
        timeout=DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            jhelper,
            "Pause App",
            "Pause API container services",
            app,
            PAUSE_ACTION,
            run_on_all_units=True,
            expected_status=["maintenance"],
            timeout=timeout,
            model=model,
        )


class _ResumeAppStep(_ActionStep):
    """Resume application API services."""

    def __init__(
        self,
        jhelper: JujuHelper,
        app: str,
        timeout=DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            jhelper,
            "Resume App",
            "Resume API container services",
            app,
            RESUME_ACTION,
            run_on_all_units=True,
            expected_status=["active"],
            timeout=timeout,
            model=model,
        )


class _ScaleAppStep(BaseStep):
    """Scale an application to a target number of units."""

    def __init__(
        self,
        jhelper: JujuHelper,
        application: str,
        scale: int,
        timeout: int = DEFAULT_ACTION_TIMEOUT,
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
            units = list(
                self.jhelper.get_application(self.application, self.model).units
            )
            self.jhelper.scale_application(self.model, self.application, self.scale)

            if self.scale == 0:
                self.jhelper.wait_units_gone(units, self.model, timeout=self.timeout)
            else:
                self.jhelper.wait_until_active(
                    self.model, apps=[self.application], timeout=self.timeout
                )
        except (
            ApplicationNotFoundException,
            JujuException,
            JujuWaitException,
            TimeoutError,
        ) as e:
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


class _RestoreAppStep(BaseStep):
    """Restore a single application from a backup (atomic restore action)."""

    def __init__(
        self,
        jhelper: JujuHelper,
        component: BackupComponent,
        target: ActionTarget,
        restore_to_time: str | None = None,
        expected_status: list[str] | None = None,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore app", f"Restoring {target.app}")
        self.jhelper = jhelper
        self.target = target
        self.restore_to_time = restore_to_time
        self.component = component
        self.model = model
        self.timeout = timeout
        self.expected_status = expected_status or ["active"]

    def run(self, context: StepContext) -> Result:
        """Restore an app using latest backup or restore-to-time."""
        try:
            leader = self.jhelper.get_leader_unit(self.target.app, self.model)
        except (LeaderNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        try:
            params = self.component.restore_params(
                self.jhelper,
                self.target,
                self.restore_to_time,
                self.timeout,
                self.model,
            )
        except (ActionFailedException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.run_action(
                leader,
                self.model,
                self.component.restore_action,
                params,
                timeout=self.timeout,
            )
            self.jhelper.wait_until_desired_status(
                self.model,
                apps=[self.target.app],
                status=self.expected_status,
                agent_status=["idle"],
                timeout=self.timeout,
            )
        except (ActionFailedException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class _BackupAppStep(BaseStep):
    """Dispatch a single application's backup action and capture the outcome."""

    def __init__(
        self,
        jhelper: JujuHelper,
        component: BackupComponent,
        target: ActionTarget,
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Backup app", f"Backing up {target.app}")
        self.jhelper = jhelper
        self.component = component
        self.target = target
        self.timeout = timeout
        self.model = model
        self.result: BackupResult | None = None

    def run(self, context: StepContext) -> Result:
        """Dispatch the backup action, recording the outcome on ``self.result``."""
        target = self.target

        try:
            action_result = self.jhelper.run_action(
                target.unit,
                self.model,
                target.action,
                timeout=self.timeout,
            )
            backup = self.component.parse_backup(action_result)
            if backup is None:
                self.result = BackupResult(
                    app=target.app,
                    unit=target.unit,
                    component=target.component,
                    error="Backup action completed without backup id.",
                )
                return Result(ResultType.FAILED, self.result.error)
            self.result = BackupResult(
                app=target.app,
                unit=target.unit,
                component=target.component,
                backup=backup,
            )
        except (ActionFailedException, JujuException) as e:
            message = str(e)
            self.result = BackupResult(
                app=target.app,
                unit=target.unit,
                component=target.component,
                error=message,
            )
            return Result(ResultType.FAILED, message)

        return Result(ResultType.COMPLETED, self.result)


class _CheckPauseResumeSupportStep(BaseStep):
    """Validate pause/resume action support for a single application."""

    def __init__(
        self,
        jhelper: JujuHelper,
        app: str,
        timeout: int = DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            "Check pause/resume support",
            "Validating pause/resume support before restore",
        )
        self.jhelper = jhelper
        self.app = app
        self.model = model
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Check that the application supports pause/resume actions."""
        try:
            actions = self.jhelper.get_application_actions(self.app, self.model)
        except (JujuException, ModelNotFoundException):
            return Result(
                ResultType.FAILED,
                f"Unable to query actions for {self.app}. No changes have been made.",
            )

        if PAUSE_ACTION not in actions or RESUME_ACTION not in actions:
            return Result(
                ResultType.FAILED,
                f"Control-plane application {self.app} does not support the "
                "'pause/resume' action required for restore. "
                "No changes have been made.",
            )

        return Result(ResultType.COMPLETED)


# ---------------------------------------------------------------------------
# Component workflows
# ---------------------------------------------------------------------------
class MySQLBackupComponent(BackupComponent):
    """MySQL backup and restore workflow."""

    name = MYSQL_CHARM
    restore_to_time_param = "restore-to-time"

    @staticmethod
    def _related_apps_for_interface(app_status: AppStatus, interface: str) -> set[str]:
        """Return related application names for an interface."""
        related_apps: set[str] = set()
        for endpoint_relations in app_status.relations.values():
            for relation in endpoint_relations:
                if relation.interface != interface:
                    continue
                related_apps.add(relation.related_app)
        return related_apps

    def _api_apps_via_routers(
        self,
        apps: Mapping[str, AppStatus],
        mysql_app: str,
        router_apps: set[str],
    ) -> set[str]:
        """Traverse mysql-router relations to resolve control-plane apps."""
        api_apps: set[str] = set()
        for router_app in router_apps:
            router_status = apps.get(router_app)
            if router_status is None:
                continue
            related_apps = self._related_apps_for_interface(
                router_status, "mysql_client"
            )
            for related_app in related_apps:
                if (
                    related_app == mysql_app
                    or related_app.endswith("-mysql-router")
                    or related_app not in apps
                ):
                    continue
                api_apps.add(related_app)
        return api_apps

    def _restore_apps(
        self, jhelper: JujuHelper, mysql_app: str, model: str
    ) -> tuple[list[str], list[str]]:
        """Resolve control-plane and router apps backed by a MySQL application."""
        status = jhelper.get_model_status(model)
        mysql_status = status.apps.get(mysql_app)
        if mysql_status is None:
            raise JujuException(f"MySQL application {mysql_app} not found in model")

        router_apps = self._related_apps_for_interface(mysql_status, "mysql_client")
        if not router_apps:
            raise JujuException(
                f"Could not resolve router applications for MySQL app {mysql_app}"
            )
        api_apps = self._api_apps_via_routers(status.apps, mysql_app, router_apps)

        if api_apps:
            return sorted(api_apps), sorted(router_apps)
        if mysql_app.endswith("-mysql"):
            api_app = mysql_app.removesuffix("-mysql")
            return [api_app], sorted(router_apps)

        raise JujuException(
            f"Could not resolve control-plane applications for MySQL app {mysql_app}"
        )

    @staticmethod
    def _current_scale(jhelper: JujuHelper, app: str, model: str) -> int:
        """Read the current MySQL unit count, failing if Juju cannot provide it."""
        try:
            return len(list(jhelper.get_application(app, model).units))
        except (ApplicationNotFoundException, JujuException):
            raise JujuException(f"Could not read current scale for {app}")

    def parse_backup_list(self, action_result: dict) -> list[BackupOutcome]:
        """Parse MySQL's tabular list-backups output."""
        backups_text = action_result.get("backups")
        if not isinstance(backups_text, str):
            return []

        backups: list[BackupOutcome] = []
        for raw_line in backups_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("backup-id") or set(line) == {"-"}:
                continue
            columns = [column.strip() for column in line.split("|")]
            if len(columns) < 3:
                continue
            backups.append(
                BackupOutcome(
                    backup_id=columns[0],
                    success=columns[2].lower() == "finished",
                )
            )
        return backups

    def resolve_backup_target(
        self, jhelper: JujuHelper, app: str, model: str, force: bool
    ) -> ActionTarget | None:
        """Prefer a secondary MySQL unit, with forced leader fallback."""
        try:
            leader = jhelper.get_leader_unit(app, model)
            units = list(jhelper.get_application(app, model).units)
        except (LeaderNotFoundException, ApplicationNotFoundException):
            LOG.warning("Could not resolve %s, skipping", app)
            return None

        try:
            result = jhelper.run_action(leader, model, MYSQL_CLUSTER_STATUS_ACTION)
            secondary = _secondary_unit_from_status(units, result)
            if secondary is not None:
                return ActionTarget(app, secondary, self.name, self.backup_action)
        except ActionFailedException as e:
            if not force:
                LOG.warning(
                    "Could not resolve backup target for %s, skipping: %s", app, e
                )
                return None
            LOG.warning(
                "Could not resolve backup target for %s, using leader (--force): %s",
                app,
                e,
            )

        return ActionTarget(app, leader, self.name, self.backup_action)

    def restore_params(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> dict[str, str]:
        """Use PITR when requested; otherwise restore the latest backup ID."""
        if restore_to_time is not None:
            return {"restore-to-time": restore_to_time}
        return self.latest_backup_params(jhelper, target, timeout, model)

    def build_restore_precheck_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Check pause/resume support for every related control-plane app."""
        api_apps, _ = self._restore_apps(jhelper, target.app, model)
        return [
            _CheckPauseResumeSupportStep(jhelper, api_app, timeout=timeout, model=model)
            for api_app in api_apps
        ]

    def build_restore_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Pause clients, restore one MySQL unit, then restore scale and clients."""
        api_apps, router_apps = self._restore_apps(jhelper, target.app, model)
        original_scale = self._current_scale(jhelper, target.app, model)
        router_scales = {
            router_app: self._current_scale(jhelper, router_app, model)
            for router_app in router_apps
        }
        return [
            *[
                _PauseAppStep(jhelper, api_app, timeout=timeout, model=model)
                for api_app in api_apps
            ],
            *[
                _ScaleAppStep(jhelper, router_app, 0, timeout=timeout, model=model)
                for router_app in router_apps
            ],
            _ScaleAppStep(jhelper, target.app, 1, timeout=timeout, model=model),
            _RestoreAppStep(
                jhelper,
                self,
                target,
                restore_to_time=restore_to_time,
                expected_status=["active", "blocked"],
                timeout=timeout,
                model=model,
            ),
            _ScaleAppStep(
                jhelper,
                target.app,
                original_scale,
                timeout=timeout,
                model=model,
            ),
            *[
                _ScaleAppStep(
                    jhelper,
                    router_app,
                    router_scales[router_app],
                    timeout=timeout,
                    model=model,
                )
                for router_app in router_apps
            ],
            *[
                _ResumeAppStep(jhelper, api_app, timeout=timeout, model=model)
                for api_app in api_apps
            ],
        ]

    def build_restore_revert_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Restore the original MySQL scale and resume client applications."""
        api_apps, router_apps = self._restore_apps(jhelper, target.app, model)
        original_scale = self._current_scale(jhelper, target.app, model)
        router_scales = {
            router_app: self._current_scale(jhelper, router_app, model)
            for router_app in router_apps
        }
        return [
            _ScaleAppStep(
                jhelper,
                target.app,
                original_scale,
                timeout=timeout,
                model=model,
            ),
            *[
                _ScaleAppStep(
                    jhelper,
                    router_app,
                    router_scales[router_app],
                    timeout=timeout,
                    model=model,
                )
                for router_app in router_apps
            ],
            *[
                _ResumeAppStep(jhelper, api_app, timeout=timeout, model=model)
                for api_app in api_apps
            ],
        ]


class VaultBackupComponent(BackupComponent):
    """Vault backup and restore workflow."""

    name = VAULT_CHARM
    restore_action = VAULT_RESTORE_ACTION

    def parse_backup_list(self, action_result: dict) -> list[BackupOutcome]:
        """Parse Vault's JSON backup-id list."""
        raw_backup_ids = action_result.get("backup-ids")
        if not isinstance(raw_backup_ids, str):
            return []
        try:
            parsed = json.loads(raw_backup_ids)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [
            BackupOutcome(backup_id=str(backup_id), success=True)
            for backup_id in parsed
        ]

    def resolve_backup_target(
        self, jhelper: JujuHelper, app: str, model: str, force: bool
    ) -> ActionTarget | None:
        """Resolve Vault backups to the leader unit."""
        try:
            leader = jhelper.get_leader_unit(app, model)
        except (LeaderNotFoundException, ApplicationNotFoundException):
            LOG.warning("Could not resolve %s, skipping", app)
            return None
        return ActionTarget(app, leader, self.name, self.backup_action)

    def restore_params(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> dict[str, str]:
        """Vault has no PITR action contract; always use the latest backup ID."""
        return self.latest_backup_params(jhelper, target, timeout, model)

    def build_restore_plan(
        self,
        jhelper: JujuHelper,
        target: ActionTarget,
        restore_to_time: str | None,
        timeout: int,
        model: str,
    ) -> list[BaseStep]:
        """Restore Vault directly, falling back to latest when PITR is requested."""
        return [
            _RestoreAppStep(
                jhelper,
                self,
                target,
                restore_to_time=restore_to_time,
                timeout=timeout,
                model=model,
            )
        ]


# ---------------------------------------------------------------------------
# Public component registry
# ---------------------------------------------------------------------------
BACKUP_COMPONENTS: list[BackupComponent] = [
    MySQLBackupComponent(),
    VaultBackupComponent(),
]


# ---------------------------------------------------------------------------
# Public top-level wrapper steps
# ---------------------------------------------------------------------------
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
                if charm_name == component.name:
                    discovered[component.name].append(app_name)

        return Result(ResultType.COMPLETED, discovered)


class ValidateStep(BaseStep):
    """Apply each component's own validation checks to its discovered apps.

    Returns a mapping keyed by component with, per application, the list of
    failed check names. Applications with an empty failure list are valid.
    """

    def __init__(
        self,
        jhelper: JujuHelper,
        discovered: dict[str, list[str]],
        model: str = OPENSTACK_MODEL,
        components: list[BackupComponent] = BACKUP_COMPONENTS,
        force: bool = False,
    ):
        super().__init__("Validate applications", "Validating backup readiness")
        self.jhelper = jhelper
        self.discovered = discovered
        self.model = model
        self.components = components
        self.force = force

    def run(self, context: StepContext) -> Result:
        """Return {'valid': {...}, 'failures': {app: [check names]}}."""
        try:
            status = self.jhelper.get_model_status(self.model)
        except (ModelNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        valid: dict[str, list[str]] = {}
        failures: dict[str, list[str]] = {}
        for component_name, apps in self.discovered.items():
            component = _component_for(component_name)
            if component is None:
                continue

            valid[component_name] = []
            for app_name in apps:
                app_status = status.apps.get(app_name)
                failed = self._failed_checks(component, app_status, self.force)
                if failed:
                    failures[app_name] = failed
                else:
                    valid[component_name].append(app_name)

        return Result(ResultType.COMPLETED, {"valid": valid, "failures": failures})

    @staticmethod
    def _failed_checks(
        component: BackupComponent,
        app_status: AppStatus | None,
        force: bool = False,
    ) -> list[str]:
        if app_status is None:
            return [check.name for check in component.validate_checks] or ["present"]
        return [
            check.name
            for check in component.validate_checks
            if not (check.forceable and force) and not check.predicate(app_status)
        ]


class ResolveActionTargetsStep(BaseStep):
    """Resolve every discovered application's leader unit for generic actions."""

    def __init__(
        self,
        jhelper: JujuHelper,
        discovered: dict[str, list[str]],
        action: Callable[[BackupComponent], str],
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            "Resolve action targets",
            "Resolving units for action targets",
        )
        self.jhelper = jhelper
        self.discovered = discovered
        self.action = action
        self.model = model

    def run(self, context: StepContext) -> Result:
        """Return resolved targets and unresolved apps across components."""
        targets: list[ActionTarget] = []
        unresolved: list[dict[str, str]] = []
        for component_name, apps in self.discovered.items():
            component = _component_for(component_name)
            if component is None:
                continue

            for app in apps:
                try:
                    leader = self.jhelper.get_leader_unit(app, self.model)
                except (LeaderNotFoundException, ApplicationNotFoundException):
                    self.update_status(context, f"skipped {app}")
                    unresolved.append({"app": app, "component": component.name})
                    continue
                targets.append(
                    ActionTarget(
                        app=app,
                        unit=leader,
                        component=component.name,
                        action=self.action(component),
                    )
                )

        return Result(
            ResultType.COMPLETED,
            {
                "targets": targets,
                "unresolved": sorted(unresolved, key=lambda item: item["app"]),
            },
        )


class RunBackupStep(BaseStep):
    """Resolve targets per component, run each backup plan, and collect results."""

    def __init__(
        self,
        jhelper: JujuHelper,
        discovered: dict[str, list[str]],
        force: bool = False,
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Run backups", "Dispatching backups across applications")
        self.jhelper = jhelper
        self.discovered = discovered
        self.force = force
        self.timeout = timeout
        self.model = model

    def _resolve_targets(
        self, context: StepContext
    ) -> tuple[list[tuple[BackupComponent, ActionTarget]], list[BackupResult]]:
        """Resolve one backup target per discovered application."""
        resolved: list[tuple[BackupComponent, ActionTarget]] = []
        failed: list[BackupResult] = []
        for component_name, apps in self.discovered.items():
            component = _component_for(component_name)
            if component is None:
                continue

            for app in apps:
                target = component.resolve_backup_target(
                    self.jhelper, app, self.model, self.force
                )
                if target is None:
                    self.update_status(context, f"skipped {app}")
                    failed.append(
                        BackupResult(
                            app=app,
                            unit="-",
                            component=component.name,
                            error="Could not resolve backup target.",
                        )
                    )
                    continue
                resolved.append((component, target))
        return resolved, failed

    def _run_backup_plan(
        self,
        component: BackupComponent,
        target: ActionTarget,
        context: StepContext,
    ) -> BackupResult:
        """Run a component's backup plan and return its BackupResult."""
        plan = component.build_backup_plan(
            self.jhelper, target, self.timeout, self.model
        )
        result: BackupResult | None = None

        for step in plan:
            step_result = step.run(context)
            if isinstance(step, _BackupAppStep) and step.result is not None:
                result = step.result
            if step_result.result_type == ResultType.FAILED:
                return result or BackupResult(
                    app=target.app,
                    unit=target.unit,
                    component=target.component,
                    error=str(step_result.message),
                )
        return result or BackupResult(
            app=target.app,
            unit=target.unit,
            component=target.component,
            error="Backup plan produced no result.",
        )

    def run(self, context: StepContext) -> Result:
        """Resolve targets, run backup plans concurrently, return results."""
        resolved, failed = self._resolve_targets(context)
        if not resolved:
            return Result(ResultType.COMPLETED, failed)

        results: list[BackupResult] = list(failed)
        with ThreadPoolExecutor(max_workers=len(resolved)) as executor:
            futures = [
                executor.submit(self._run_backup_plan, component, target, context)
                for component, target in resolved
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return Result(ResultType.COMPLETED, results)


class ListBackupsStep(BaseStep):
    """Dispatch list-backups actions concurrently and collect inventories."""

    def __init__(
        self,
        jhelper: JujuHelper,
        targets: list[ActionTarget],
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("List backups", "Listing backups across applications")
        self.jhelper = jhelper
        self.targets = targets
        self.timeout = timeout
        self.model = model

    def _list_one(
        self,
        target: ActionTarget,
        action_name: str,
        parse_backups: Callable[[dict], list[BackupOutcome]] | None,
    ) -> BackupInventory:
        """Dispatch a single list-backups action and parse backup IDs."""
        try:
            result = self.jhelper.run_action(
                target.unit, self.model, action_name, timeout=self.timeout
            )
            backups = parse_backups(result) if parse_backups is not None else []
            return BackupInventory(
                app=target.app,
                unit=target.unit,
                component=target.component,
                backups=backups,
            )
        except (ActionFailedException, JujuException) as e:
            return BackupInventory(
                app=target.app,
                unit=target.unit,
                component=target.component,
                error=str(e),
            )

    def run(self, context: StepContext) -> Result:
        """Dispatch list-backups concurrently, returning inventory entries."""
        if not self.targets:
            return Result(ResultType.COMPLETED, [])

        inventories: list[BackupInventory] = []
        with ThreadPoolExecutor(max_workers=len(self.targets)) as executor:
            futures = []
            for target in self.targets:
                component = _component_for(target.component)
                if component is None:
                    continue

                futures.append(
                    executor.submit(
                        self._list_one,
                        target,
                        component.list_action,
                        component.parse_backup_list,
                    )
                )
            for future in as_completed(futures):
                inventories.append(future.result())

        return Result(ResultType.COMPLETED, inventories)


class RestoreStep(BaseStep):
    """Drive every component's restore plan, reverting on failure."""

    def __init__(
        self,
        jhelper: JujuHelper,
        discovered: dict[str, list[str]],
        restore_to_time: str | None = None,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore applications", "Restoring applications from backup")
        self.jhelper = jhelper
        self.discovered = discovered
        self.restore_to_time = restore_to_time
        self.timeout = timeout
        self.model = model

    def _run_plan(self, plan: list[BaseStep], context: StepContext) -> None:
        """Run a plan of steps, raising RuntimeError on the first failure."""
        for step in plan:
            result = step.run(context)
            if result.result_type == ResultType.FAILED:
                raise RuntimeError(result.message)

    def _resolve_targets(self, context: StepContext) -> list[ActionTarget]:
        """Resolve one backup target per discovered application."""
        result = ResolveActionTargetsStep(
            self.jhelper,
            self.discovered,
            action=lambda component: component.restore_action,
            model=self.model,
        ).run(context)
        unresolved = result.message["unresolved"]
        if unresolved:
            raise RuntimeError(
                f"Could not resolve restore target for {unresolved[0]['app']}"
            )
        return result.message["targets"]

    def _prepare_restore(
        self,
        component: BackupComponent,
        target: ActionTarget,
    ) -> PreparedRestore:
        """Build restore and compensation plans before any mutation occurs."""
        plan = component.build_restore_plan(
            self.jhelper,
            target,
            self.restore_to_time,
            self.timeout,
            self.model,
        )
        revert_plan = component.build_restore_revert_plan(
            self.jhelper,
            target,
            self.timeout,
            self.model,
        )
        return PreparedRestore(component, target, plan, revert_plan)

    def _run_revert_plan(self, plan: list[BaseStep], context: StepContext) -> list[str]:
        """Attempt every compensation step and return all failure messages."""
        errors: list[str] = []
        for step in plan:
            try:
                result = step.run(context)
                if result.result_type == ResultType.FAILED:
                    errors.append(str(result.message))
            except (
                JujuException,
                ActionFailedException,
                LeaderNotFoundException,
                ModelNotFoundException,
            ) as e:
                errors.append(str(e))
        return errors

    def _restore_one(
        self,
        prepared: PreparedRestore,
        context: StepContext,
    ) -> RestoreResult:
        try:
            self._run_plan(prepared.plan, context)
            return RestoreResult(
                app=prepared.target.app,
                component=prepared.component.name,
                success=True,
            )
        except (
            RuntimeError,
            JujuException,
            ActionFailedException,
            LeaderNotFoundException,
            ModelNotFoundException,
        ) as e:
            revert_errors = self._run_revert_plan(prepared.revert_plan, context)
            rollback_error = "; ".join(revert_errors) or None
            if revert_errors:
                LOG.warning(
                    "Revert failed for %s: %s", prepared.target.app, rollback_error
                )
            return RestoreResult(
                app=prepared.target.app,
                component=prepared.component.name,
                success=False,
                error=str(e),
                reverted=bool(prepared.revert_plan) and not revert_errors,
                rollback_error=rollback_error,
            )

    def run(self, context: StepContext) -> Result:
        """Precheck all targets, then restore each sequentially, aggregating."""
        try:
            resolved = self._resolve_targets(context)
            targets: list[tuple[BackupComponent, ActionTarget]] = []
            for target in resolved:
                component = _component_for(target.component)
                if component is not None:
                    targets.append((component, target))

            for component, target in targets:
                precheck = component.build_restore_precheck_plan(
                    self.jhelper, target, self.timeout, self.model
                )
                self._run_plan(precheck, context)

            prepared = [
                self._prepare_restore(component, target)
                for component, target in targets
            ]
        except (
            RuntimeError,
            JujuException,
            ActionFailedException,
            LeaderNotFoundException,
            ModelNotFoundException,
        ) as e:
            return Result(ResultType.FAILED, str(e))

        if not prepared:
            return Result(ResultType.COMPLETED, [])

        results: list[RestoreResult] = []
        for index, restore in enumerate(prepared):
            self.update_status(context, f"restoring {restore.target.app}")
            outcome = self._restore_one(restore, context)
            results.append(outcome)
            if outcome.success:
                continue
            results.extend(
                RestoreResult(
                    app=pending.target.app,
                    component=pending.component.name,
                    success=False,
                    error=(
                        "Restore not attempted because restore failed for "
                        f"{restore.target.app}."
                    ),
                )
                for pending in prepared[index + 1 :]
            )
            break

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
