# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass
from pathlib import Path

import click
import pydantic
from jubilant.statustypes import AppStatus
from rich.console import Console

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.checks import (
    JujuLoginCheck,
    VerifyBootstrappedCheck,
    run_preflight_checks,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    delete_config,
    read_config,
    run_plan,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuException, JujuHelper, JujuWaitException
from sunbeam.core.manifest import AddManifestStep, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.questions import (
    ConfirmQuestion,
    PasswordPromptQuestion,
    PromptQuestion,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformInitStep,
    TerraformStateLockedException,
)
from sunbeam.steps.backup_restore import (
    BACKUP_COMPONENTS,
    S3_ENDPOINT,
    S3_RELATION_VALIDATION_CHECK,
)
from sunbeam.steps.openstack import TOPOLOGY_KEY
from sunbeam.utils import click_option_show_hints

BACKUP_STORAGE_KEY = "BackupStorage"
BACKUP_STORAGE_PLAN = "backup-storage-plan"
BACKUP_STORAGE_TFVARS = "TerraformVarsBackupStorage"
_MANAGED_S3_KEY = "managed-s3-integrations"  # {target_app: integrator_app}
S3_INTEGRATOR_CHARM = "s3-integrator"
DEFAULT_S3_PATH = "/"
DEFAULT_S3_REGION = "us-east-2"
DEFAULT_S3_ENDPOINT = "https://s3.us-east-2.amazonaws.com"

console = Console()
LOG = logging.getLogger(__name__)


@dataclass
class S3Integration:
    """Data class representing an S3 integration for a target application."""

    app_name: str
    integrator_app: str
    target_endpoint: str


class BackupStorageConfig(pydantic.BaseModel):
    configure_s3_integrators: bool = pydantic.Field(
        default=False,
        alias="configure-managed-s3-integrators",
        validation_alias="configure_managed_s3_integrators",
    )
    bucket: str = ""
    path: str = DEFAULT_S3_PATH
    region: str = DEFAULT_S3_REGION
    endpoint: str = DEFAULT_S3_ENDPOINT
    access_key: str = pydantic.Field(
        default="", alias="access-key", validation_alias="access_key", repr=False
    )
    secret_key: str = pydantic.Field(
        default="", alias="secret-key", validation_alias="secret_key", repr=False
    )


def load_backup_storage(deployment: Deployment) -> dict:
    """Read backup storage state, defaulting to an unconfigured deployment."""
    try:
        return read_config(deployment.get_client(), BACKUP_STORAGE_KEY)
    except ConfigItemNotFoundException:
        return {}


def managed_integrations(deployment: Deployment) -> dict[str, str]:
    """Return recorded ownership independently of deployment readiness."""
    managed = load_backup_storage(deployment).get(_MANAGED_S3_KEY, {})
    if not isinstance(managed, dict):
        return {}
    return {str(key): str(value) for key, value in managed.items()}


class BackupStorage:
    """Configure and track Sunbeam-owned S3 integrations."""

    def __init__(self) -> None:
        self._s3_integrations_cache: list[S3Integration] | None = None

    def get_database_topology(self, deployment: Deployment) -> str:
        """Read the database topology selected during bootstrap."""
        return read_config(deployment.get_client(), TOPOLOGY_KEY)["database"]

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        integrations = self._s3_integrations(deployment)
        return sorted({integration.integrator_app for integration in integrations})

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: BackupStorageConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        jhelper = deployment.get_juju_helper()
        model_uuid = jhelper.get_model_uuid(OPENSTACK_MODEL)
        integrations = self._s3_integrations(deployment)
        tfvars = {
            "enable-backup-storage": True,
            "openstack-model-uuid": model_uuid,
            "s3-integrator-apps": sorted(
                {integration.integrator_app for integration in integrations}
            ),
            "s3-integrations": {
                integration.app_name: {
                    "integrator_app": integration.integrator_app,
                    "target_endpoint": integration.target_endpoint,
                }
                for integration in integrations
            },
        }
        if config.configure_s3_integrators:
            tfvars["s3-integrator-config"] = self._s3_integrator_config_tfvar(
                config, integrations
            )
            tfvars["s3-integrator-secret-data"] = self._s3_integrator_secret_data_tfvar(
                config, integrations
            )
        return tfvars

    def configure_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Set up, update or remove Sunbeam-managed backup storage."""
        self._s3_integrations_cache = None
        state = load_backup_storage(deployment)
        if state.get("enabled", False) or managed_integrations(deployment):
            action = PromptQuestion[str](
                "Backup configuration action",
                choices=["keep", "update", "remove"],
                default_value="keep",
            )
            action.console = console
            action.show_hint = show_hints
            choice = action.ask()
            if choice == "keep":
                return
            if choice == "remove":
                managed = self._s3_load_managed_integrations(deployment)
                console.print(
                    "Sunbeam-managed S3 integrations to remove: "
                    + (
                        ", ".join(
                            f"{app} -> {target}"
                            for app, target in sorted(managed.items())
                        )
                        or "None"
                    )
                )
                console.print(
                    "External integrations and S3 backup data will be preserved."
                )
                confirm = ConfirmQuestion(
                    "Remove Sunbeam-managed backup integrations?", default_value=False
                )
                confirm.console = console
                if not confirm.ask():
                    return
                self._s3_integrations_cache = [
                    S3Integration(app, target, S3_ENDPOINT)
                    for app, target in sorted(managed.items())
                ]
                self.remove(deployment, show_hints)
                return
        else:
            confirm = ConfirmQuestion(
                "Enable and deploy Sunbeam-managed backup integrations?",
                default_value=False,
            )
            confirm.console = console
            if not confirm.ask():
                return
        config = self._prompt_s3_configuration(deployment, show_hints)
        self.setup(deployment, config, show_hints)

    def setup(
        self, deployment: Deployment, config: BackupStorageConfig, show_hints: bool
    ) -> None:
        """Deploy managed integrations through the core Terraform plan."""
        run_preflight_checks(
            [
                VerifyBootstrappedCheck(deployment.get_client()),
                JujuLoginCheck(deployment.juju_account),
            ],
            console,
        )
        ctx: click.Context | None = click.get_current_context()
        manifest_path: Path | None = None
        while ctx is not None:
            if ctx.command.name == "configure":
                manifest_path = ctx.params.get("manifest_path")
                break
            ctx = ctx.parent
        manifest = deployment.get_manifest(manifest_path)
        tfvars = self.set_tfvars_on_enable(deployment, config)
        plan: list[BaseStep] = []
        if manifest_path:
            plan.append(AddManifestStep(deployment.get_client(), manifest_path))
        plan.extend(
            [
                TerraformInitStep(deployment.get_tfhelper(BACKUP_STORAGE_PLAN)),
                ApplyBackupStorageStep(deployment, manifest, tfvars),
            ]
        )
        run_plan(plan, console, show_hints)

    def remove(self, deployment: Deployment, show_hints: bool) -> None:
        """Remove only Terraform-owned integrations and retain state on failure."""
        run_preflight_checks(
            [
                VerifyBootstrappedCheck(deployment.get_client()),
                JujuLoginCheck(deployment.juju_account),
            ],
            console,
        )
        run_plan(
            [
                TerraformInitStep(deployment.get_tfhelper(BACKUP_STORAGE_PLAN)),
                RemoveBackupStorageStep(deployment),
            ],
            console,
            show_hints,
        )

    def _s3_integrations(self, deployment: Deployment) -> list[S3Integration]:
        """Compute managed S3 integrations once per command invocation."""
        cache = getattr(self, "_s3_integrations_cache", None)
        if cache is not None:
            return cache
        jhelper = deployment.get_juju_helper()
        apps = jhelper.get_model_status(OPENSTACK_MODEL).apps
        managed = self._s3_load_managed_integrations(deployment)
        database_topology = self.get_database_topology(deployment)
        integrations = self._s3_build_integrations(
            jhelper, apps, managed, database_topology
        )
        # Retain owned targets absent from discovery so applying an update cannot
        # silently destroy them, and partial applies remain recoverable.
        discovered = {integration.app_name for integration in integrations}
        integrations.extend(
            S3Integration(app, integrator, S3_ENDPOINT)
            for app, integrator in managed.items()
            if app not in discovered
        )
        self._s3_integrations_cache = integrations
        return integrations

    def _s3_build_integrations(
        self,
        jhelper: JujuHelper,
        apps: dict[str, AppStatus],
        managed_integrators: dict[str, str] | None = None,
        database_topology: str | None = None,
    ) -> list[S3Integration]:
        targets = self._s3_discover_relation_targets(apps, database_topology)
        managed_integrators = managed_integrators or {}
        integrations: list[S3Integration] = []

        for app_name in targets:
            app_status = apps[app_name]
            endpoint = self._s3_target_endpoint_for_app(app_status)
            is_related = bool(self._s3_relation_consumers(jhelper, app_name, endpoint))
            expected_integrator = managed_integrators.get(
                app_name, self._s3_integrator_app_name(app_name)
            )
            owned = app_name in managed_integrators

            if not owned and (is_related or expected_integrator in apps):
                console.print(
                    (
                        f"[yellow]Warning:[/yellow] Skipping backup storage "
                        f"configuration for {app_name}: "
                        "existing S3 setup is managed externally."
                    )
                )
                continue

            integrations.append(
                S3Integration(
                    app_name=app_name,
                    integrator_app=expected_integrator,
                    target_endpoint=endpoint,
                )
            )
        return integrations

    def _s3_discover_relation_targets(
        self,
        apps: dict[str, AppStatus],
        database_topology: str | None = None,
    ) -> list[str]:
        """Return applications eligible for backup S3 integration."""
        target_charms = set(self._s3_relation_target_components().keys())
        targets: list[str] = []
        for app_name, app_status in apps.items():
            if app_status.charm_name not in target_charms:
                continue
            if app_status.charm_name == "mysql-k8s":
                if database_topology == "single" and app_name != "mysql":
                    continue
                if database_topology == "multi" and app_name == "mysql":
                    continue
            targets.append(app_name)
        return targets

    def _s3_relation_consumers(
        self, jhelper: JujuHelper, app_name: str, endpoint: str
    ) -> set[str]:
        """Return whether app already has any s3 relation."""
        try:
            relation_map = jhelper.get_relation_map(app_name, endpoint, OPENSTACK_MODEL)
        except JujuException:
            return set()
        return {consumer for consumer in relation_map.values() if consumer}

    def _s3_target_endpoint_for_app(self, app_status: AppStatus) -> str:
        """Return the S3 endpoint used by an app based on backup component mapping."""
        charm_name = app_status.charm_name
        return self._s3_relation_target_components().get(charm_name, S3_ENDPOINT)

    def _s3_relation_target_components(self) -> dict[str, str]:
        """Return backup components that require S3 relation validation."""
        return {
            component.name: S3_ENDPOINT
            for component in BACKUP_COMPONENTS
            if any(
                check.name == S3_RELATION_VALIDATION_CHECK.name
                for check in component.validate_checks
            )
        }

    def _s3_integrator_app_name(self, app_name: str) -> str:
        """Return per-application s3-integrator app name for a target app."""
        service_name = app_name.removesuffix("-mysql")
        return f"{service_name}-s3-integrator"

    def _prompt_s3_configuration(
        self, deployment: Deployment, show_hints: bool
    ) -> BackupStorageConfig:
        """Prompt for managed s3-integrator configuration."""
        config = BackupStorageConfig()
        if not self._s3_integrations(deployment):
            return config

        configure = ConfirmQuestion(
            "Configure all s3-integrators?",
            default_value=False,
            description=(
                "Configure bucket, endpoint, region, and credentials for all "
                "s3-integrator apps."
            ),
        )
        configure.console = console
        configure.show_hint = show_hints
        if not configure.ask():
            return config

        config.configure_s3_integrators = True
        config.bucket = self._ask_prompt("S3 bucket", show_hints)
        config.path = self._ask_prompt(
            "S3 path prefix", show_hints, default=DEFAULT_S3_PATH
        )
        config.region = self._ask_prompt(
            "S3 region", show_hints, default=DEFAULT_S3_REGION
        )
        config.endpoint = self._ask_prompt(
            "S3 endpoint", show_hints, default=DEFAULT_S3_ENDPOINT
        )
        config.access_key = self._ask_password("S3 access key", show_hints)
        config.secret_key = self._ask_password("S3 secret key", show_hints)
        self._validate_s3_config(config)
        return config

    def _ask_prompt(self, question: str, show_hints: bool, default: str = "") -> str:
        prompt: PromptQuestion[str] = PromptQuestion(question, default_value=default)
        prompt.console = console
        prompt.show_hint = show_hints
        value = prompt.ask() or ""
        return value.strip()

    def _ask_password(self, question: str, show_hints: bool) -> str:
        prompt: PasswordPromptQuestion[str] = PasswordPromptQuestion(
            question, password=True
        )
        prompt.console = console
        prompt.show_hint = show_hints
        value = prompt.ask() or ""
        return value.strip()

    def _validate_s3_config(self, config: BackupStorageConfig) -> None:
        """Validate prompted S3 configuration."""
        if not config.bucket:
            raise click.ClickException("S3 bucket is required.")
        if not config.access_key:
            raise click.ClickException("S3 access key is required.")
        if not config.secret_key:
            raise click.ClickException("S3 secret key is required.")
        if not config.endpoint.startswith(("http://", "https://")):
            raise click.ClickException(
                "S3 endpoint must start with http:// or https://."
            )

    def _s3_integrator_config_tfvar(
        self,
        config: BackupStorageConfig,
        integrations: list[S3Integration],
    ) -> dict[str, dict[str, str]]:
        """Build per-integrator s3-integrator charm config tfvar."""
        if not config.configure_s3_integrators:
            return {}

        base_path = (config.path or DEFAULT_S3_PATH).strip("/")
        path_prefix = f"/{base_path}" if base_path else ""
        return {
            integration.integrator_app: {
                "bucket": config.bucket,
                "path": f"{path_prefix}/{integration.app_name}",
                "region": config.region,
                "endpoint": config.endpoint,
            }
            for integration in integrations
        }

    def _s3_integrator_secret_data_tfvar(
        self,
        config: BackupStorageConfig,
        integrations: list[S3Integration],
    ) -> dict[str, dict[str, str]]:
        """Build per-integrator secret payload for juju_secret resources."""
        if not config.configure_s3_integrators:
            return {}

        return {
            integration.integrator_app: {
                "access-key": config.access_key,
                "secret-key": config.secret_key,
            }
            for integration in integrations
        }

    def _s3_load_managed_integrations(self, deployment: Deployment) -> dict[str, str]:
        return managed_integrations(deployment)


class ApplyBackupStorageStep(BaseStep):
    """Apply storage configuration, recording ownership before resource creation."""

    def __init__(self, deployment: Deployment, manifest: Manifest, tfvars: dict):
        super().__init__("Configure backup storage", "Applying backup storage plan")
        self.deployment = deployment
        self.manifest = manifest
        self.tfvars = tfvars

    def run(self, context: StepContext) -> Result:
        """Keep intended ownership available even after a partial apply."""
        state = load_backup_storage(self.deployment)
        owned = managed_integrations(self.deployment)
        owned.update(
            {
                app: value["integrator_app"]
                for app, value in self.tfvars["s3-integrations"].items()
            }
        )
        state[_MANAGED_S3_KEY] = owned
        state.setdefault("enabled", False)
        client = self.deployment.get_client()
        update_config(client, BACKUP_STORAGE_KEY, state)
        try:
            self.deployment.get_tfhelper(
                BACKUP_STORAGE_PLAN
            ).update_tfvars_and_apply_tf(
                client,
                self.manifest,
                tfvar_config=BACKUP_STORAGE_TFVARS,
                override_tfvars=self.tfvars,
                reporter=context.reporter,
            )
            self.deployment.get_juju_helper().wait_until_desired_status(
                OPENSTACK_MODEL,
                sorted(set(owned.values())),
                status=["active", "blocked"],
                timeout=900,
            )
        except (
            TerraformException,
            TerraformStateLockedException,
            JujuWaitException,
            TimeoutError,
        ) as e:
            return Result(ResultType.FAILED, str(e))
        state["enabled"] = True
        update_config(client, BACKUP_STORAGE_KEY, state)
        return Result(ResultType.COMPLETED)


class RemoveBackupStorageStep(BaseStep):
    """Destroy the dedicated storage plan and clear ownership after removal."""

    def __init__(self, deployment: Deployment):
        super().__init__("Remove backup storage", "Removing managed S3 integrations")
        self.deployment = deployment

    def run(self, context: StepContext) -> Result:
        """Retain ownership and configuration if destruction or waiting fails."""
        apps = sorted(set(managed_integrations(self.deployment).values()))
        try:
            self.deployment.get_tfhelper(BACKUP_STORAGE_PLAN).destroy(
                reporter=context.reporter
            )
            self.deployment.get_juju_helper().wait_application_gone(
                apps, OPENSTACK_MODEL, timeout=900
            )
        except (
            TerraformException,
            TerraformStateLockedException,
            JujuWaitException,
            TimeoutError,
        ) as e:
            return Result(ResultType.FAILED, str(e))
        client = self.deployment.get_client()
        try:
            delete_config(client, BACKUP_STORAGE_TFVARS)
        except ConfigItemNotFoundException:
            pass
        update_config(
            client, BACKUP_STORAGE_KEY, {"enabled": False, _MANAGED_S3_KEY: {}}
        )
        return Result(ResultType.COMPLETED)


class RefreshBackupStorageStep(BaseStep):
    """Refresh owned integrators without rediscovery or configuration prompts."""

    def __init__(self, deployment: Deployment, manifest: Manifest):
        super().__init__("Refresh backup storage", "Refreshing managed S3 integrations")
        self.deployment = deployment
        self.manifest = manifest

    def is_skip(self, context: StepContext) -> Result:
        """Skip deployments without enabled managed storage."""
        return Result(
            ResultType.COMPLETED
            if load_backup_storage(self.deployment).get("enabled", False)
            else ResultType.SKIPPED
        )

    def run(self, context: StepContext) -> Result:
        """Preserve persisted S3 configuration and monitor owned applications only."""
        tfhelper = self.deployment.get_tfhelper(BACKUP_STORAGE_PLAN)
        apps = sorted(set(managed_integrations(self.deployment).values()))
        try:
            tfhelper.init()
            tfhelper.update_partial_tfvars_and_apply_tf(
                self.deployment.get_client(),
                self.manifest,
                [S3_INTEGRATOR_CHARM],
                BACKUP_STORAGE_TFVARS,
                reporter=context.reporter,
            )
            self.deployment.get_juju_helper().wait_until_desired_status(
                OPENSTACK_MODEL,
                apps,
                status=["active", "blocked", "unknown"],
                timeout=900,
            )
        except (
            TerraformException,
            TerraformStateLockedException,
            JujuWaitException,
            TimeoutError,
        ) as e:
            return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


@click.command("backup")
@click_option_show_hints
@click.pass_obj
def configure_backup(deployment: Deployment, show_hints: bool) -> None:
    """Set up, update or remove Sunbeam-managed backup storage."""
    BackupStorage().configure_cmd(deployment, show_hints)
