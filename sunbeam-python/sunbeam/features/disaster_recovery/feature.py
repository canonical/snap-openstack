# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass
from pathlib import Path

import click
from jubilant.statustypes import AppStatus
from packaging.version import Version
from rich.console import Console

from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import ApplicationStatusOverlay, JujuException, JujuHelper
from sunbeam.core.manifest import (
    CharmManifest,
    FeatureConfig,
    SoftwareConfig,
    TerraformManifest,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
    TerraformPlanLocation,
)
from sunbeam.steps.backup_restore import (
    BACKUP_COMPONENTS,
    S3_ENDPOINT,
    S3_INTEGRATOR_CHARM,
    S3_RELATION_VALIDATION_CHECK,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj
from sunbeam.versions import S3_INTEGRATOR_CHANNEL

_MANAGED_S3_KEY = "managed-s3-integrations"  # {target_app: integrator_app}

console = Console()
LOG = logging.getLogger(__name__)


@dataclass
class S3Integration:
    """Data class representing an S3 integration for a target application."""

    app_name: str
    integrator_app: str
    target_endpoint: str


class DisasterRecoveryFeature(OpenStackControlPlaneFeature):
    version = Version("0.0.1")

    name = "disaster-recovery"
    generally_available = False
    tf_plan_location = TerraformPlanLocation.FEATURE_REPO

    _s3_integrations_cache: list["S3Integration"] | None = None

    def default_software_overrides(self) -> SoftwareConfig:
        """Feature software configuration."""
        return SoftwareConfig(
            charms={
                S3_INTEGRATOR_CHARM: CharmManifest(channel=S3_INTEGRATOR_CHANNEL),
            },
            terraform={
                self.tfplan: TerraformManifest(
                    source=Path(__file__).parent / "etc" / self.tfplan_dir
                )
            },
        )

    def manifest_attributes_tfvar_map(self) -> dict:
        """Manifest attributes terraformvars map."""
        return {
            self.tfplan: {
                "charms": {
                    S3_INTEGRATOR_CHARM: {
                        "channel": "s3-integrator-channel",
                        "revision": "s3-integrator-revision",
                        "config": "s3-integrator-config",
                    }
                }
            }
        }

    def set_application_names(self, deployment: Deployment) -> list:
        """Application names handled by the terraform plan."""
        integrations = self._s3_integrations(deployment)
        return sorted({integration.integrator_app for integration in integrations})

    def set_tfvars_on_enable(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to enable the application."""
        jhelper = deployment.get_juju_helper()
        model_uuid = jhelper.get_model_uuid(OPENSTACK_MODEL)
        integrations = self._s3_integrations(deployment)
        return {
            "enable-disaster-recovery": True,
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

    def set_tfvars_on_disable(self, deployment: Deployment) -> dict:
        """Set terraform variables to disable the application."""
        jhelper = deployment.get_juju_helper()
        model_uuid = jhelper.get_model_uuid(OPENSTACK_MODEL)
        integrations = self._s3_integrations(deployment)
        return {
            "enable-disaster-recovery": False,
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

    def set_tfvars_on_resize(
        self, deployment: Deployment, config: FeatureConfig
    ) -> dict:
        """Set terraform variables to resize the application."""
        return {}

    def get_app_status_overlay_on_enable(
        self, deployment: Deployment
    ) -> dict[str, ApplicationStatusOverlay]:
        """Accept blocked status for DR-managed s3-integrator apps on enable."""
        return {
            app_name: {"status": ["active", "blocked"]}
            for app_name in self.set_application_names(deployment)
        }

    def post_enable(
        self, deployment: Deployment, config: FeatureConfig, show_hints: bool
    ) -> None:
        """Perform post-enable actions for the feature."""
        integrations = self._s3_integrations(deployment)
        self._s3_save_managed_integrations(deployment, integrations)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Enable disaster recovery service."""
        self._s3_integrations_cache = None
        self.enable_feature(deployment, FeatureConfig(), show_hints)

    @click.command()
    @click_option_show_hints
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, show_hints: bool) -> None:
        """Disable disaster recovery service."""
        self._s3_integrations_cache = None
        self.disable_feature(deployment, show_hints)

    def _s3_integrations(self, deployment: Deployment) -> list[S3Integration]:
        """Compute DR S3 integrations once per command invocation.

        The result is cached on the feature instance so the multiple lifecycle
        callbacks in a single enable/disable run reuse the same computation
        instead of re-querying Juju relations every time.
        """
        cache = getattr(self, "_s3_integrations_cache", None)
        if cache is not None:
            return cache
        jhelper = deployment.get_juju_helper()
        apps = jhelper.get_model_status(OPENSTACK_MODEL).apps
        managed = self._s3_load_managed_integrations(deployment)
        integrations = self._s3_build_integrations(jhelper, apps, managed)
        self._s3_integrations_cache = integrations
        return integrations

    def _s3_build_integrations(
        self,
        jhelper: JujuHelper,
        apps: dict[str, AppStatus],
        managed_integrators: dict[str, str] | None = None,
    ) -> list[S3Integration]:
        targets = self._s3_discover_relation_targets(apps)
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
                        f"[yellow]Warning:[/yellow] Skipping disaster recovery for "
                        f"{app_name}: existing S3 setup is not managed by "
                        "disaster recovery."
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

    def _s3_discover_relation_targets(self, apps: dict[str, AppStatus]) -> list[str]:
        """Return applications eligible for DR S3 integration."""
        target_charms = set(self._s3_relation_target_components().keys())
        targets: list[str] = []
        for app_name, app_status in apps.items():
            if app_status.charm_name not in target_charms:
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

    def _s3_load_managed_integrations(self, deployment: Deployment) -> dict[str, str]:
        info = self.get_feature_info(deployment.get_client())
        managed = info.get(_MANAGED_S3_KEY, {})
        if not isinstance(managed, dict):
            return {}
        return {str(k): str(v) for k, v in managed.items()}

    def _s3_save_managed_integrations(
        self, deployment: Deployment, integrations: list[S3Integration]
    ) -> None:
        self.update_feature_info(
            deployment.get_client(),
            {
                _MANAGED_S3_KEY: {
                    integration.app_name: integration.integrator_app
                    for integration in integrations
                }
            },
        )
