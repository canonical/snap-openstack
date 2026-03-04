# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

from rich.console import Console
from rich.status import Status

from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
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
from sunbeam.steps.k8s import DeployK8SApplicationStep
from sunbeam.steps.microceph import DeployMicrocephApplicationStep
from sunbeam.steps.microovn import DeployMicroOVNApplicationStep
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
        deployed_k8s_apps = self.get_charm_deployed_versions(OPENSTACK_MODEL)
        deployed_machine_apps = self.get_charm_deployed_versions(
            self.deployment.openstack_machines_model
        )

        all_deployed_apps = deployed_k8s_apps.copy()
        all_deployed_apps.update(deployed_machine_apps)
        LOG.debug(f"All deployed apps: {all_deployed_apps}")
        if self.is_track_changed_for_any_charm(all_deployed_apps):
            error_msg = (
                "Manifest has track values that require upgrades, rerun with "
                "option --upgrade-release for release upgrades."
            )
            return Result(ResultType.FAILED, error_msg)

        result = self.refresh_apps(deployed_k8s_apps, OPENSTACK_MODEL, status)
        if result.result_type == ResultType.FAILED:
            return result

        result = self.refresh_apps(
            deployed_machine_apps, self.deployment.openstack_machines_model, status
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

        if is_maas_deployment(self.deployment):
            plan.extend(
                [
                    OpenStackPatchLoadBalancerServicesIPPoolStep(
                        self.client,
                        self.deployment.public_api_label,  # type: ignore [attr-defined]
                    )
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
