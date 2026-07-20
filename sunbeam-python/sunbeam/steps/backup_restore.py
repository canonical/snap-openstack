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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tenacity
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
RESTORE_ACTION_ATTEMPTS = 3
RESTORE_ACTION_RETRY_DELAY = 15


@dataclass
class ActionTarget:
    """An application and the unit chosen to run an action against."""

    app: str
    unit: str
    component: str
    action: str


@dataclass
class BackupOutcome:
    """A single backup entry in a backup inventory."""

    backup_id: str
    success: bool | None = None


@dataclass
class BackupResult:
    """The outcome of attempting a backup for a single application."""

    app: str
    unit: str
    component: str
    backup: BackupOutcome | None = None
    error: str | None = None


@dataclass
class BackupInventory:
    """The result of listing backup IDs for a single application target."""

    app: str
    unit: str
    component: str
    backups: list[BackupOutcome] | None = None
    error: str | None = None


@dataclass
class RestoreResult:
    """The outcome of attempting a restore for a single application."""

    app: str
    component: str
    success: bool
    error: str | None = None
    reverted: bool = False


@dataclass
class ValidationCheck:
    """An application-readiness check."""

    name: str
    predicate: Callable[[object], bool]
    forceable: bool = False


ResolveTargetFn = Callable[[JujuHelper, str, str, bool], "ActionTarget | None"]
BackupPlanFn = Callable[
    [JujuHelper, "BackupComponent", "ActionTarget", bool, int, str],
    list[BaseStep],
]
RestorePlanFn = Callable[
    [JujuHelper, "BackupComponent", "ActionTarget", str | None, bool, int, str],
    list[BaseStep],
]
RevertPlanFn = Callable[
    [JujuHelper, "BackupComponent", "ActionTarget", bool, int, str],
    list[BaseStep],
]
PrecheckPlanFn = Callable[
    [JujuHelper, "BackupComponent", "ActionTarget", bool, int, str],
    list[BaseStep],
]


@dataclass
class BackupComponent:
    """Descriptor for a kind of stateful application that can be backed up."""

    name: str

    resolve_backup_target: ResolveTargetFn
    parse_backup_list: Callable[[dict], list[BackupOutcome]]
    parse_backup: Callable[[dict], BackupOutcome | None]
    build_backup_plan: BackupPlanFn
    build_restore_plan: RestorePlanFn
    build_restore_revert_plan: RevertPlanFn | None = None
    build_restore_precheck_plan: PrecheckPlanFn | None = None

    backup_action: str = BACKUP_ACTION
    restore_action: str = RESTORE_ACTION
    list_action: str = LIST_BACKUPS_ACTION

    restore_to_time_param: str | None = None
    backup_id_param: str = BACKUP_RESULT_ID_KEY

    validate_checks: list[ValidationCheck] = field(default_factory=list)


def _component_for(name: str) -> BackupComponent | None:
    return next((c for c in BACKUP_COMPONENTS if c.name == name), None)


# ---------------------------------------------------------------------------
# Validation predicates
# ---------------------------------------------------------------------------
def _is_app_active(app_status: object) -> bool:
    """Return whether application workload status is active."""
    status = getattr(app_status, "app_status", None)
    current = getattr(status, "current", None)
    if not isinstance(current, str):
        return True
    return current == "active"


def _is_related_to_s3(app_status: object) -> bool:
    """Return whether the application is related to S3 via the endpoint."""
    relations = getattr(app_status, "relations", None) or {}
    endpoint_relations = relations.get(S3_ENDPOINT, [])
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
# Backup result parsing
# ---------------------------------------------------------------------------
def _parse_mysql_backups(action_result: dict) -> list[BackupOutcome]:
    """Parse MySQL list-backups output table and return finished backup IDs."""
    backups_text = action_result.get("backups")
    if backups_text is None:
        backups_text = (action_result.get("results") or {}).get("backups")
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
        if columns[2].lower() != "finished":
            backups.append(BackupOutcome(backup_id=columns[0], success=False))
            continue
        backups.append(BackupOutcome(columns[0], success=True))
    return backups


def _parse_backup(action_result: dict) -> BackupOutcome | None:
    """Parse create-backup output and return the backup ID."""
    backup_id = action_result.get(BACKUP_RESULT_ID_KEY)
    if not isinstance(backup_id, str):
        return None
    return BackupOutcome(backup_id=backup_id, success=True)


def _parse_vault_backups(action_result: dict) -> list[BackupOutcome]:
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
    return [
        BackupOutcome(backup_id=str(backup_id), success=True) for backup_id in parsed
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def _latest_backup(backups: list[BackupOutcome]) -> str | None:
    """Return the lexicographically latest successful backup ID, if present."""
    successful = [b for b in backups if b.success]
    if not successful:
        return None
    return sorted(successful, key=lambda b: b.backup_id)[-1].backup_id


def _api_app_for_mysql(app_name: str) -> str:
    """Return the API application name for a given MySQL application name."""
    if app_name.endswith("-mysql"):
        return app_name.replace("-mysql", "", 1)
    return app_name


def _current_scale(jhelper: JujuHelper, app: str, model: str) -> int:
    """Read the current unit count for an application, live."""
    try:
        return len(list(jhelper.get_application(app, model).units))
    except (ApplicationNotFoundException, JujuException):
        LOG.warning("Could not read current scale for %s, assuming 1", app)
        return 1


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------
def _resolve_mysql_backup_target(
    jhelper: JujuHelper,
    app: str,
    model: str,
    force: bool,
) -> ActionTarget | None:
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

    try:
        result = jhelper.run_action(leader, model, MYSQL_CLUSTER_STATUS_ACTION)
        secondary = _secondary_unit_from_status(units, result)
        if secondary is not None:
            return ActionTarget(
                app=app,
                unit=secondary,
                component=MYSQL_CHARM,
                action=BACKUP_ACTION,
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

    return ActionTarget(
        app=app,
        unit=leader,
        component=MYSQL_CHARM,
        action=BACKUP_ACTION,
    )


def _resolve_vault_backup_target(
    jhelper: JujuHelper,
    app: str,
    model: str,
    force: bool,
) -> ActionTarget | None:
    """Resolve the Vault backup target to the leader unit."""
    try:
        leader = jhelper.get_leader_unit(app, model)
    except (LeaderNotFoundException, ApplicationNotFoundException):
        LOG.warning("Could not resolve %s, skipping", app)
        return None

    return ActionTarget(
        app=app,
        unit=leader,
        component=VAULT_CHARM,
        action=BACKUP_ACTION,
    )


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
        force: bool = False,
        timeout: int = DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(name, description)
        self.jhelper = jhelper
        self.model = model
        self.app = app
        self.action_name = action_name
        self.run_on_all_units = run_on_all_units
        self.force = force
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
            if not self.force:
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
        force: bool = False,
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
            force=force,
            timeout=timeout,
            model=model,
        )


class _ResumeAppStep(_ActionStep):
    """Resume application API services."""

    def __init__(
        self,
        jhelper: JujuHelper,
        app: str,
        force: bool = False,
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
            force=force,
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
        force: bool = False,
        timeout: int = DEFAULT_ACTION_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ) -> None:
        super().__init__("Scale App", f"Scaling {application} to {scale} unit(s)")
        self.jhelper = jhelper
        self.application = application
        self.scale = scale
        self.timeout = timeout
        self.model = model
        self.force = force

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


class _RestoreAppStep(BaseStep):
    """Restore a single application from a backup (atomic restore action)."""

    def __init__(
        self,
        jhelper: JujuHelper,
        component: "BackupComponent",
        target: ActionTarget,
        restore_to_time: str | None = None,
        expected_status: list[str] | None = None,
        force: bool = False,
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
        self.force = force
        self.expected_status = expected_status or ["active"]

    def run(self, context: StepContext) -> Result:
        """Restore an app using latest backup or restore-to-time."""
        try:
            leader = self.jhelper.get_leader_unit(self.target.app, self.model)
        except (LeaderNotFoundException, JujuException) as e:
            return Result(ResultType.FAILED, str(e))

        params: dict[str, str | bool] = {"force": True} if self.force else {}
        restore_to_time_param = self.component.restore_to_time_param

        if self.restore_to_time is not None and restore_to_time_param is not None:
            params[restore_to_time_param] = self.restore_to_time
        else:
            try:
                list_result = self.jhelper.run_action(
                    leader,
                    self.model,
                    self.component.list_action,
                    timeout=self.timeout,
                )
            except (ActionFailedException, JujuException) as e:
                return Result(ResultType.FAILED, str(e))

            latest = _latest_backup(self.component.parse_backup_list(list_result))
            if latest is None:
                return Result(
                    ResultType.FAILED,
                    f"No finished backups found for {self.target.app}.",
                )
            params[self.component.backup_id_param] = latest

        try:
            tenacity.Retrying(
                reraise=True,
                stop=tenacity.stop_after_attempt(RESTORE_ACTION_ATTEMPTS),
                wait=tenacity.wait_fixed(RESTORE_ACTION_RETRY_DELAY),
                retry=tenacity.retry_if_exception_type(ActionFailedException),
            )(
                self.jhelper.run_action,
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
        component: "BackupComponent",
        target: ActionTarget,
        force: bool = False,
        timeout: int = DEFAULT_BACKUP_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Backup app", f"Backing up {target.app}")
        self.jhelper = jhelper
        self.component = component
        self.target = target
        self.force = force
        self.timeout = timeout
        self.model = model
        self.result: BackupResult | None = None

    def run(self, context: StepContext) -> Result:
        """Dispatch the backup action, recording the outcome on ``self.result``."""
        target = self.target
        params: dict[str, str | bool] = {"force": True} if self.force else {}

        try:
            action_result = self.jhelper.run_action(
                target.unit,
                self.model,
                target.action,
                params,
                timeout=self.timeout,
            )
            backup = self.component.parse_backup(action_result)
            self.result = BackupResult(
                app=target.app,
                unit=target.unit,
                component=target.component,
                backup=backup,
            )
        except Exception as e:
            message = str(e)
            in_progress = "timed out waiting for results" in message.lower()
            self.result = BackupResult(
                app=target.app,
                unit=target.unit,
                component=target.component,
                backup=BackupOutcome(backup_id="", success=None)
                if in_progress
                else None,
                error=message,
            )

        return Result(ResultType.COMPLETED, self.result)


class _CheckPauseResumeSupportStep(BaseStep):
    """Validate pause/resume action support for a single application."""

    def __init__(
        self,
        jhelper: JujuHelper,
        app: str,
        force: bool = False,
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
        self.force = force
        self.timeout = timeout

    def run(self, context: StepContext) -> Result:
        """Fail fast if the application does not support pause/resume actions."""
        try:
            actions = self.jhelper.get_application_actions(self.app, self.model)
        except (JujuException, ModelNotFoundException):
            return Result(
                ResultType.FAILED,
                f"Unable to query actions for {self.app}. No changes have been made.",
            )

        if PAUSE_ACTION not in actions or RESUME_ACTION not in actions:
            if not self.force:
                return Result(
                    ResultType.FAILED,
                    f"Control-plane application {self.app} does not support the "
                    "'pause/resume' action required for restore. "
                    "No changes have been made.",
                )
            else:
                LOG.warning(
                    "Control-plane application %s does not support pause/resume"
                    ", proceeding (--force).",
                    self.app,
                )

        return Result(ResultType.COMPLETED)


# ---------------------------------------------------------------------------
# Per-component plan builders
# ---------------------------------------------------------------------------
def _build_mysql_backup_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Build the MySQL backup plan: a single create-backup action."""
    return [
        _BackupAppStep(
            jhelper, component, target, force=force, timeout=timeout, model=model
        ),
    ]


def _build_vault_backup_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Build the Vault backup plan: a single create-backup action."""
    return [
        _BackupAppStep(
            jhelper, component, target, force=force, timeout=timeout, model=model
        ),
    ]


def _build_mysql_restore_precheck_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Precheck that the MySQL app's API app supports pause/resume."""
    api_app = _api_app_for_mysql(target.app)
    return [
        _CheckPauseResumeSupportStep(
            jhelper, api_app, force=force, timeout=timeout, model=model
        )
    ]


def _build_mysql_restore_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    restore_to_time: str | None,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Build the MySQL restore plan: pause, scale down, restore, scale up, resume."""
    api_app = _api_app_for_mysql(target.app)
    original_scale = _current_scale(jhelper, target.app, model)

    return [
        _PauseAppStep(jhelper, api_app, force=force, timeout=timeout, model=model),
        _ScaleAppStep(
            jhelper, target.app, 1, force=force, timeout=timeout, model=model
        ),
        _RestoreAppStep(
            jhelper,
            component,
            target,
            restore_to_time=restore_to_time,
            expected_status=["active", "blocked"],
            force=force,
            timeout=timeout,
            model=model,
        ),
        _ScaleAppStep(
            jhelper,
            target.app,
            original_scale,
            force=force,
            timeout=timeout,
            model=model,
        ),
        _ResumeAppStep(
            jhelper,
            api_app,
            force=force,
            timeout=timeout,
            model=model,
        ),
    ]


def _build_mysql_restore_revert_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Build the revert plan for a failed MySQL restore: scale back up, resume."""
    api_app = _api_app_for_mysql(target.app)
    original_scale = _current_scale(jhelper, target.app, model)

    return [
        _ScaleAppStep(
            jhelper,
            target.app,
            original_scale,
            force=force,
            timeout=timeout,
            model=model,
        ),
        _ResumeAppStep(jhelper, api_app, force=force, timeout=timeout, model=model),
    ]


def _build_vault_restore_plan(
    jhelper: JujuHelper,
    component: BackupComponent,
    target: ActionTarget,
    restore_to_time: str | None,
    force: bool,
    timeout: int,
    model: str,
) -> list[BaseStep]:
    """Build the Vault restore plan: a single restore step (no pause/scale)."""
    return [
        _RestoreAppStep(
            jhelper,
            component,
            target,
            restore_to_time=restore_to_time,
            force=force,
            timeout=timeout,
            model=model,
        ),
    ]


# ---------------------------------------------------------------------------
# Public component registry
# ---------------------------------------------------------------------------
BACKUP_COMPONENTS: list[BackupComponent] = [
    BackupComponent(
        name=MYSQL_CHARM,
        resolve_backup_target=_resolve_mysql_backup_target,
        parse_backup_list=_parse_mysql_backups,
        parse_backup=_parse_backup,
        build_backup_plan=_build_mysql_backup_plan,
        build_restore_plan=_build_mysql_restore_plan,
        build_restore_revert_plan=_build_mysql_restore_revert_plan,
        build_restore_precheck_plan=_build_mysql_restore_precheck_plan,
        validate_checks=[APP_READY_VALIDATION_CHECK, S3_RELATION_VALIDATION_CHECK],
    ),
    BackupComponent(
        name=VAULT_CHARM,
        resolve_backup_target=_resolve_vault_backup_target,
        parse_backup_list=_parse_vault_backups,
        parse_backup=_parse_backup,
        build_backup_plan=_build_vault_backup_plan,
        build_restore_plan=_build_vault_restore_plan,
        validate_checks=[APP_READY_VALIDATION_CHECK, S3_RELATION_VALIDATION_CHECK],
        restore_action=VAULT_RESTORE_ACTION,
    ),
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
            valid[component_name] = []
            if component is None:
                continue
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
        component: BackupComponent, app_status: object | None, force: bool = False
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
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__(
            "Resolve action targets",
            "Resolving units for action targets",
        )
        self.jhelper = jhelper
        self.discovered = discovered
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
                        action=component.list_action,
                    )
                )

        return Result(
            ResultType.COMPLETED,
            {
                "targets": targets,
                "unresolved": sorted(unresolved, key=lambda item: item["app"]),
            },
        )


class RunBackupsStep(BaseStep):
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
    ) -> list[tuple[BackupComponent, ActionTarget]]:
        """Resolve one backup target per discovered application."""
        resolved: list[tuple[BackupComponent, ActionTarget]] = []
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
                    continue
                resolved.append((component, target))
        return resolved

    def _run_backup_plan(
        self,
        component: BackupComponent,
        target: ActionTarget,
        context: StepContext,
    ) -> BackupResult:
        """Run a component's backup plan and return its BackupResult.

        Any step failure short of the result-bearing BackupAppStep is encoded
        as a failed BackupResult for the target.
        """
        plan = component.build_backup_plan(
            self.jhelper, component, target, self.force, self.timeout, self.model
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
        resolved = self._resolve_targets(context)
        if not resolved:
            return Result(ResultType.COMPLETED, [])

        results: list[BackupResult] = []
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
        except Exception as e:
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
        force: bool = False,
        timeout: int = DEFAULT_RESTORE_TIMEOUT,
        model: str = OPENSTACK_MODEL,
    ):
        super().__init__("Restore applications", "Restoring applications from backup")
        self.jhelper = jhelper
        self.discovered = discovered
        self.restore_to_time = restore_to_time
        self.timeout = timeout
        self.model = model
        self.force = force

    def _run_plan(self, plan: list[BaseStep], context: StepContext) -> None:
        """Run a plan of steps, raising RuntimeError on the first failure."""
        for step in plan:
            result = step.run(context)
            if result.result_type == ResultType.FAILED:
                raise RuntimeError(result.message)

    def _resolve_targets(
        self, context: StepContext
    ) -> list[tuple[BackupComponent, ActionTarget]]:
        """Resolve one backup target per discovered application."""
        resolved: list[tuple[BackupComponent, ActionTarget]] = []
        for component_name, apps in self.discovered.items():
            component = _component_for(component_name)
            if component is None:
                continue
            for app in apps:
                target = ActionTarget(
                    app=app,
                    unit=self.jhelper.get_leader_unit(app, self.model),
                    component=component.name,
                    action=component.restore_action,
                )
                resolved.append((component, target))
        return resolved

    def _restore_one(
        self, component: BackupComponent, target: ActionTarget, context: StepContext
    ) -> RestoreResult:
        revert_plan: list[BaseStep] = []
        if component.build_restore_revert_plan is not None:
            revert_plan = component.build_restore_revert_plan(
                self.jhelper, component, target, self.force, self.timeout, self.model
            )
        plan = component.build_restore_plan(
            self.jhelper,
            component,
            target,
            self.restore_to_time,
            self.force,
            self.timeout,
            self.model,
        )
        try:
            self._run_plan(plan, context)
            return RestoreResult(app=target.app, component=component.name, success=True)
        except Exception as e:
            reverted = False
            if revert_plan:
                try:
                    self._run_plan(revert_plan, context)
                    reverted = True
                except Exception as revert_error:
                    LOG.warning("Revert failed for %s: %s", target.app, revert_error)
            return RestoreResult(
                app=target.app,
                component=component.name,
                success=False,
                error=str(e),
                reverted=reverted,
            )

    def run(self, context: StepContext) -> Result:
        """Precheck all targets, then restore each sequentially, aggregating."""
        resolved = self._resolve_targets(context)
        if not resolved:
            return Result(ResultType.COMPLETED, [])

        for component, target in resolved:
            if component.build_restore_precheck_plan is None:
                continue
            precheck = component.build_restore_precheck_plan(
                self.jhelper, component, target, self.force, self.timeout, self.model
            )
            try:
                self._run_plan(precheck, context)
            except Exception as e:
                return Result(ResultType.FAILED, str(e))

        results: list[RestoreResult] = []
        for component, target in resolved:
            self.update_status(context, f"restoring {target.app}")
            results.append(self._restore_one(component, target, context))

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
