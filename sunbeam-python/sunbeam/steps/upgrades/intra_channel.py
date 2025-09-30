# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from rich.console import Console
from rich.status import Status

from sunbeam.core.common import BaseStep, Result, ResultType
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuStepHelper
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

    def refresh_apps(self, apps: dict, model: str) -> None:
        """Refresh apps in the model.

        If the charm has no revision in manifest and channel mentioned in manifest
        and the deployed app is same, run juju refresh.
        Otherwise ignore so that terraform plan apply will take care of charm upgrade.
        """
        for app_name, (charm, channel, _) in apps.items():
            manifest_charm = self.manifest.core.software.charms.get(charm)
            if not manifest_charm:
                for _, feature in self.manifest.get_features():
                    manifest_charm = feature.software.charms.get(charm)
                    if manifest_charm:
                        break
            if not manifest_charm:
                continue

            if not manifest_charm.revision and manifest_charm.channel == channel:
                LOG.debug(f"Running refresh for app {app_name}")
                # refresh() checks for any new revision and updates if available
                self.jhelper.charm_refresh(app_name, model)

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

        self.refresh_apps(deployed_k8s_apps, OPENSTACK_MODEL)
        self.refresh_apps(
            deployed_machine_apps, self.deployment.openstack_machines_model
        )
        return Result(ResultType.COMPLETED)


class LatestInChannelCoordinator(UpgradeCoordinator):
    """Coordinator for refreshing charms in their current channel."""

    def get_plan(self) -> list[BaseStep]:
        """Return the upgrade plan."""
        plan = [
            LatestInChannel(self.deployment, self.jhelper, self.manifest),
            TerraformInitStep(self.deployment.get_tfhelper("openstack-plan")),
            ReapplyOpenStackTerraformPlanStep(
                self.client,
                self.deployment.get_tfhelper("openstack-plan"),
                self.jhelper,
                self.manifest,
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

        plan.extend([OpenStackPatchLoadBalancerServicesIPStep(self.client)])

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
                ),
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
