# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    update_status_background,
)
from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    build_pre_status_overlay,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformInitStep
from sunbeam.features.interface.v1.base import is_maas_deployment
from sunbeam.steps.cinder_volume import DeployCinderVolumeApplicationStep
from sunbeam.steps.hypervisor import ReapplyHypervisorTerraformPlanStep
from sunbeam.steps.k8s import (
    DeployK8SApplicationStep,
    EnsureDefaultL2AdvertisementMutedStep,
    EnsureL2AdvertisementByHostStep,
)
from sunbeam.steps.microceph import DeployMicrocephApplicationStep
from sunbeam.steps.microovn import DeployMicroOVNApplicationStep
from sunbeam.steps.mysql import MySQLCharmUpgradeStep
from sunbeam.steps.openstack import (
    OpenStackPatchLoadBalancerServicesIPPoolStep,
    OpenStackPatchLoadBalancerServicesIPStep,
    ReapplyOpenStackTerraformPlanStep,
    build_overlay_dict,
)
from sunbeam.steps.sunbeam_machine import DeploySunbeamMachineApplicationStep
from sunbeam.steps.upgrades.base import UpgradeCoordinator, UpgradeFeatures

LOG = logging.getLogger(__name__)
console = Console()

INFRA_APPS = ["mysql-k8s"]

# Snap-based charm applications that expose a refresh-snap action.
# These need to be refreshed explicitly after the charm refresh because
# their snaps are held to prevent spontaneous snapd auto-refreshes.
SNAP_APPS_MACHINE_MODEL: list[str] = [
    "openstack-hypervisor",
    "openstack-network-agents",
    "cinder-volume",
    "epa-orchestrator",
    "manila-data",
    # consul-client apps deployed by the instance-recovery feature;
    # up to 3 apps depending on how many networks are in use.
    "consul-client-management",
    "consul-client-tenant",
    "consul-client-storage",
]

# Snap-based charm applications deployed in the infra model (MAAS only).
SNAP_APPS_INFRA_MODEL: list[str] = [
    "sunbeam-clusterd",
]


class LatestInChannel(BaseStep, JujuStepHelper):
    def __init__(self, deployment: Deployment, jhelper: JujuHelper, manifest: Manifest):
        """Upgrade all charms to latest in current channel.

        :jhelper: Helper for interacting with pylibjuju
        """
        super().__init__(
            "In channel upgrade", "Upgrade charms to latest revision in current channel"
        )
        self.deployment = deployment
        self.jhelper = jhelper
        self.manifest = manifest

    def is_skip(self, status: Status | None = None) -> Result:
        """Step can be skipped if nothing needs refreshing."""
        return Result(ResultType.COMPLETED)

    def is_track_changed_for_any_charm(self, deployed_apps: dict):
        """Check if chanel track is same in manifest and deployed app."""
        for app_name, (charm, channel, _) in deployed_apps.items():
            charm_manifest = self.manifest.core.software.charms.get(charm)
            if not charm_manifest:
                for _, feature in self.manifest.get_features():
                    charm_manifest = feature.software.charms.get(charm)
                    if not charm_manifest:
                        continue
            if not charm_manifest:
                LOG.debug(f"Charm not present in manifest: {charm}")
                continue

            channel_from_manifest = charm_manifest.channel or ""
            if not channel_from_manifest:
                # No channel specified in manifest (revision only), skip track check
                continue
            track_from_manifest = channel_from_manifest.split("/")[0]
            track_from_deployed_app = channel.split("/")[0]
            # Compare tracks
            if track_from_manifest != track_from_deployed_app:
                LOG.debug(
                    f"Channel track for app {app_name} different in manifest "
                    "and actual deployed"
                )
                return True

        return False

    def _wait_after_refresh(
        self,
        refreshed_apps: list[str],
        model: str,
        pre_refresh_status: dict[str, str],
        status: Status | None = None,
    ) -> Result:
        """Wait for refreshed apps to settle after a juju refresh.

        Each app is accepted in its pre-refresh workload status OR active so
        that apps that were in a non-active state before the refresh are not
        held against an impossible condition.
        """
        if not refreshed_apps:
            return Result(ResultType.COMPLETED)

        LOG.debug(f"Waiting for apps {refreshed_apps} in model {model}")
        if model == OPENSTACK_MODEL:
            overlay = build_pre_status_overlay(
                refreshed_apps,
                pre_refresh_status,
                build_overlay_dict(refreshed_apps),
            )
            LOG.debug(f"Wait overlay for {model}: {overlay}")
            status_queue: queue.Queue[str] = queue.Queue()
            task = update_status_background(self, refreshed_apps, status_queue, status)
            try:
                self.jhelper.wait_until_desired_status(
                    model,
                    refreshed_apps,
                    timeout=3600,  # 60 minutes
                    queue=status_queue,
                    overlay=overlay,
                )
            except (JujuWaitException, TimeoutError) as e:
                LOG.warning(str(e))
                return Result(ResultType.FAILED, str(e))
            finally:
                task.stop()
        else:
            # For machine applications, accept the pre-refresh status plus
            # active and unknown.
            try:
                for app_name in refreshed_apps:
                    prior = pre_refresh_status.get(app_name, "active")
                    accepted = list({prior, "active", "unknown"})
                    LOG.debug(
                        f"Waiting for {app_name} in {model} "
                        f"with accepted_status={accepted}"
                    )
                    self.jhelper.wait_application_ready(
                        app_name,
                        model,
                        accepted_status=accepted,
                        timeout=1800,  # 30 minutes
                    )
            except TimeoutError as e:
                LOG.warning(str(e))
                return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def refresh_apps(
        self, apps: dict, model: str, status: Status | None = None
    ) -> Result:
        """Refresh apps in the model.

        If there is no manifest charm entry, refresh the charm to latest revision.
        If manifest charm exists, refresh using channel and revision from manifest.

        After the refresh the wait accepts the app's pre-refresh workload status
        OR active.  This avoids false timeouts for apps that were in a
        non-active state (e.g. waiting, blocked) before the refresh was issued.
        """
        # Snapshot the workload status of every app before the refresh so the
        # post-refresh wait can use it as one of the accepted statuses.
        pre_refresh_status: dict[str, str] = {}
        try:
            pre_refresh_status = self.jhelper.snapshot_workload_status(
                model, list(apps)
            )
        except Exception:
            LOG.debug("Could not fetch pre-refresh status", exc_info=True)
        LOG.debug(f"Pre-refresh workload status in {model}: {pre_refresh_status}")

        refreshed_apps = []
        for app_name, (charm, channel, _) in apps.items():
            # Skip infra apps, they are refreshed via `sunbeam cluster refresh <app>`
            if charm in INFRA_APPS:
                continue
            manifest_charm = self.manifest.core.software.charms.get(charm)
            if not manifest_charm:
                for _, feature in self.manifest.get_features():
                    manifest_charm = feature.software.charms.get(charm)
                    if manifest_charm:
                        break

            if not manifest_charm:
                LOG.debug(f"Running refresh for app {app_name} (no manifest entry)")
                self.jhelper.charm_refresh(app_name, model)
                refreshed_apps.append(app_name)
            else:
                LOG.debug(f"Running refresh for app {app_name} with manifest config")
                self.jhelper.charm_refresh(
                    app_name,
                    model,
                    channel=manifest_charm.channel,
                    revision=manifest_charm.revision,
                )
                refreshed_apps.append(app_name)

        return self._wait_after_refresh(
            refreshed_apps, model, pre_refresh_status, status
        )

    def run(self, status: Status | None = None) -> Result:
        """Refresh all charms identified as needing a refresh.

        If the manifest has charm channel and revision, terraform apply should update
        the charms.
        If the manifest has only charm, then juju refresh is required if channel is
        same as deployed charm, otherwise juju upgrade charm.
        """
        deployed_infra_apps: dict = {}
        if is_maas_deployment(self.deployment):
            deployed_infra_apps = self.get_charm_deployed_versions(
                self.deployment.infra_model
            )
        deployed_k8s_apps = self.get_charm_deployed_versions(OPENSTACK_MODEL)
        deployed_machine_apps = self.get_charm_deployed_versions(
            self.deployment.openstack_machines_model
        )

        all_deployed_apps = deployed_k8s_apps.copy()
        all_deployed_apps.update(deployed_machine_apps)
        all_deployed_apps.update(deployed_infra_apps)
        LOG.debug(f"All deployed apps: {all_deployed_apps}")
        if self.is_track_changed_for_any_charm(all_deployed_apps):
            error_msg = (
                "Manifest has track values that require upgrades, rerun with "
                "option --upgrade-release for release upgrades."
            )
            return Result(ResultType.FAILED, error_msg)

        if is_maas_deployment(self.deployment):
            result = self.refresh_apps(
                deployed_infra_apps, self.deployment.infra_model, status
            )
            if result.result_type == ResultType.FAILED:
                return result

        result = self.refresh_apps(deployed_k8s_apps, OPENSTACK_MODEL, status)
        if result.result_type == ResultType.FAILED:
            return result

        result = self.refresh_apps(
            deployed_machine_apps, self.deployment.openstack_machines_model, status
        )
        if result.result_type == ResultType.FAILED:
            return result

        return Result(ResultType.COMPLETED)


class ReapplyInfraModelConfigStep(BaseStep, JujuStepHelper):
    """Re-apply manifest config to openstack-infra model applications.

    The infra model is not managed by Terraform, so config changes from
    the manifest must be applied directly via Juju.
    """

    # Map of Juju application name -> charm name in manifest
    INFRA_APPS: dict[str, str] = {
        "sunbeam-clusterd": "sunbeam-clusterd",
        "tls-operator": "self-signed-certificates",
    }

    def __init__(self, deployment: Deployment, jhelper: JujuHelper, manifest: Manifest):
        super().__init__(
            "Reapply infra model config",
            "Re-applying config to openstack-infra model applications",
        )
        self.deployment = deployment
        self.jhelper = jhelper
        self.manifest = manifest

    def is_skip(self, status: Status | None = None) -> Result:
        """Skip if not a MAAS deployment."""
        if not is_maas_deployment(self.deployment):
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Apply manifest charm config to each infra model application."""
        model = self.deployment.infra_model  # type: ignore[attr-defined]
        for app_name, charm_name in self.INFRA_APPS.items():
            charm_manifest = self.manifest.core.software.charms.get(charm_name)
            if not charm_manifest or not charm_manifest.config:
                LOG.debug(
                    f"No manifest config for {charm_name}, skipping config reapply"
                )
                continue
            LOG.debug(
                f"Reapplying config for {app_name} in {model}: {charm_manifest.config}"
            )
            self.jhelper.set_app_config(app_name, model, charm_manifest.config)
        return Result(ResultType.COMPLETED)


class RefreshSnapStep(BaseStep, JujuStepHelper):
    """Run refresh-snap action on all snap-based charm units.

    This step must run after the charm refresh so that the new refresh-snap
    action handler is present.  Snaps are held at their current revision by
    the charm to prevent spontaneous snapd auto-refreshes; this step explicitly
    triggers a snap refresh on every unit before the Terraform plans are applied.
    """

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        super().__init__(
            "Refresh snaps",
            "Run refresh-snap action on snap-based charm units",
        )
        self.deployment = deployment
        self.jhelper = jhelper

    def _refresh_snap_for_apps(
        self, apps: list[str], model: str, status: Status | None = None
    ) -> Result:
        """Run refresh-snap action on all units of *apps* in *model*."""
        for app_name in apps:
            try:
                application = self.jhelper.get_application(app_name, model)
            except ApplicationNotFoundException:
                LOG.debug(
                    "Application %s not found in %s, skipping snap refresh",
                    app_name,
                    model,
                )
                continue

            for unit_name in application.units:
                LOG.debug("Running refresh-snap on %s in %s", unit_name, model)
                self.update_status(status, f"refreshing snap on {unit_name}")
                try:
                    self.jhelper.run_action(
                        unit_name,
                        model,
                        "refresh-snap",
                        timeout=600,
                    )
                except ActionFailedException as e:
                    LOG.warning("refresh-snap failed on %s: %s", unit_name, e)
                    return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Run refresh-snap on all snap-based charm applications."""
        result = self._refresh_snap_for_apps(
            SNAP_APPS_MACHINE_MODEL,
            self.deployment.openstack_machines_model,
            status,
        )
        if result.result_type == ResultType.FAILED:
            return result

        if is_maas_deployment(self.deployment):
            result = self._refresh_snap_for_apps(
                SNAP_APPS_INFRA_MODEL,
                self.deployment.infra_model,  # type: ignore[attr-defined]
                status,
            )
            if result.result_type == ResultType.FAILED:
                return result

        return Result(ResultType.COMPLETED)


class LatestInChannelCoordinator(UpgradeCoordinator):
    """Coordinator for refreshing charms in their current channel."""

    def get_plan(self) -> list[BaseStep]:
        """Return the upgrade plan."""
        plan = [
            LatestInChannel(self.deployment, self.jhelper, self.manifest),
            ReapplyInfraModelConfigStep(self.deployment, self.jhelper, self.manifest),
            RefreshSnapStep(self.deployment, self.jhelper),
            # Microceph introduces new offer urls for rgw and so microceph
            # plan need to be applied before openstack plan
            TerraformInitStep(self.deployment.get_tfhelper("microceph-plan")),
            DeployMicrocephApplicationStep(
                self.deployment,
                self.client,
                self.deployment.get_tfhelper("microceph-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
            TerraformInitStep(self.deployment.get_tfhelper("openstack-plan")),
            ReapplyOpenStackTerraformPlanStep(
                self.deployment,
                self.client,
                self.deployment.get_tfhelper("openstack-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
            TerraformInitStep(self.deployment.get_tfhelper("sunbeam-machine-plan")),
            DeploySunbeamMachineApplicationStep(
                self.deployment,
                self.client,
                self.deployment.get_tfhelper("sunbeam-machine-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
        ]

        if is_maas_deployment(self.deployment):
            from sunbeam.provider.maas.client import MaasClient  # noqa: PLC0415
            from sunbeam.provider.maas.steps import (  # noqa: PLC0415
                MaasCreateLoadBalancerIPPoolsStep,
                MaasDeployK8SApplicationStep,
            )

            maas_client = MaasClient.from_deployment(self.deployment)
            plan.extend(
                [
                    TerraformInitStep(self.deployment.get_tfhelper("k8s-plan")),
                    MaasDeployK8SApplicationStep(
                        self.deployment,  # type: ignore [arg-type]
                        self.client,
                        maas_client,
                        self.deployment.get_tfhelper("k8s-plan"),
                        self.jhelper,
                        self.manifest,
                        self.deployment.openstack_machines_model,
                    ),
                    EnsureDefaultL2AdvertisementMutedStep(
                        self.deployment, self.client, self.jhelper
                    ),
                    MaasCreateLoadBalancerIPPoolsStep(
                        self.deployment,  # type: ignore [arg-type]
                        self.client,
                        maas_client,
                    ),
                    EnsureL2AdvertisementByHostStep(
                        self.deployment,
                        self.client,
                        self.jhelper,
                        self.deployment.openstack_machines_model,
                        Networks.INTERNAL,
                        self.deployment.internal_ip_pool,  # type: ignore [attr-defined]
                    ),
                    EnsureL2AdvertisementByHostStep(
                        self.deployment,
                        self.client,
                        self.jhelper,
                        self.deployment.openstack_machines_model,
                        Networks.PUBLIC,
                        self.deployment.public_ip_pool,  # type: ignore [attr-defined]
                    ),
                    OpenStackPatchLoadBalancerServicesIPPoolStep(
                        self.client,
                        self.deployment.public_api_label,  # type: ignore [attr-defined]
                    ),
                ]
            )
        else:
            plan.extend(
                [
                    TerraformInitStep(self.deployment.get_tfhelper("k8s-plan")),
                    DeployK8SApplicationStep(
                        self.deployment,
                        self.client,
                        self.deployment.get_tfhelper("k8s-plan"),
                        self.jhelper,
                        self.manifest,
                        self.deployment.openstack_machines_model,
                        refresh=True,
                    ),
                ]
            )

        ovn_manager = self.deployment.get_ovn_manager()
        plan.extend(
            [OpenStackPatchLoadBalancerServicesIPStep(self.client, ovn_manager)]
        )

        network_nodes = []
        microovn_roles = ovn_manager.get_roles_for_microovn()
        for role in microovn_roles:
            network_nodes.extend(
                self.client.cluster.list_nodes_by_role(role.name.lower())
            )

        if len(network_nodes):
            plan.extend(
                [
                    TerraformInitStep(self.deployment.get_tfhelper("microovn-plan")),
                    DeployMicroOVNApplicationStep(
                        self.deployment,
                        self.client,
                        self.deployment.get_tfhelper("microovn-plan"),
                        self.jhelper,
                        self.manifest,
                        self.deployment.openstack_machines_model,
                        ovn_manager,
                    ),
                ]
            )

        plan.extend(
            [
                TerraformInitStep(self.deployment.get_tfhelper("microceph-plan")),
                DeployMicrocephApplicationStep(
                    self.deployment,
                    self.client,
                    self.deployment.get_tfhelper("microceph-plan"),
                    self.jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
                TerraformInitStep(self.deployment.get_tfhelper("cinder-volume-plan")),
                DeployCinderVolumeApplicationStep(
                    self.deployment,
                    self.client,
                    self.deployment.get_tfhelper("cinder-volume-plan"),
                    self.jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
                TerraformInitStep(self.deployment.get_tfhelper("hypervisor-plan")),
                ReapplyHypervisorTerraformPlanStep(
                    self.client,
                    self.deployment.get_tfhelper("hypervisor-plan"),
                    self.jhelper,
                    self.manifest,
                    self.deployment.openstack_machines_model,
                ),
                UpgradeFeatures(self.deployment, upgrade_release=False),
            ]
        )

        return plan


class MySQLInChannelUpgradeCoordinator(UpgradeCoordinator):
    """Coordinator for refreshing mysql-k8s charm in its current channel."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        jhelper: JujuHelper,
        manifest: Manifest,
        reset_mysql_upgrade_state: bool,
    ):
        super().__init__(deployment, client, jhelper, manifest)
        self.reset_mysql_upgrade_state = reset_mysql_upgrade_state

    def get_plan(self) -> list[BaseStep]:
        """Return the upgrade plan."""
        plan = [
            MySQLCharmUpgradeStep(
                self.deployment,
                self.client,
                self.jhelper,
                self.manifest,
                self.reset_mysql_upgrade_state,
            ),
            TerraformInitStep(self.deployment.get_tfhelper("openstack-plan")),
            ReapplyOpenStackTerraformPlanStep(
                self.deployment,
                self.client,
                self.deployment.get_tfhelper("openstack-plan"),
                self.jhelper,
                self.manifest,
                self.deployment.openstack_machines_model,
            ),
        ]
        return plan
