# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Ingress (traefik-k8s) refresh steps for `sunbeam cluster refresh ingress`."""

import logging

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType, StepContext
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper, JujuStepHelper, JujuWaitException
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.steps.charm_upgrade import check_charm_needs_refresh
from sunbeam.steps.openstack import build_overlay_dict
from sunbeam.versions import TRAEFIK_CHANNEL

LOG = logging.getLogger(__name__)

TRAEFIK_CHARM = "traefik-k8s"


class IngressCharmRefreshStep(BaseStep, JujuStepHelper):
    """Refresh traefik-k8s applications to the manifest channel/revision.

    Used by `sunbeam cluster refresh ingress`: refreshes each deployed
    traefik application (traefik, traefik-public, traefik-rgw) following
    the same decision logic as the vault refresh (skip when already at
    the latest revision, fail on unsupported track changes).
    """

    def __init__(self, deployment: Deployment, jhelper: JujuHelper, manifest: Manifest):
        super().__init__(
            "Ingress charm refresh",
            "Refreshing traefik-k8s applications",
        )
        self.deployment = deployment
        self.jhelper = jhelper
        self.manifest = manifest
        self.traefik_apps: list[str] = []

    def is_skip(self, context: StepContext) -> Result:
        """Skip when no traefik-k8s application is deployed."""
        deployed_apps = self.get_charm_deployed_versions(OPENSTACK_MODEL)
        self.traefik_apps = sorted(
            app
            for app, (charm, _, _) in deployed_apps.items()
            if charm == TRAEFIK_CHARM
        )
        if not self.traefik_apps:
            return Result(ResultType.SKIPPED, "No traefik-k8s applications deployed")
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Refresh the traefik applications that need a refresh."""
        refreshed_apps = []
        for app in self.traefik_apps:
            decision = check_charm_needs_refresh(
                self.jhelper,
                self.manifest,
                TRAEFIK_CHARM,
                OPENSTACK_MODEL,
                app,
                default_channel=TRAEFIK_CHANNEL,
                support_track_upgrades=True,
            )
            if decision.app_not_deployed:
                continue
            if decision.result.result_type == ResultType.FAILED:
                return decision.result
            if decision.result.result_type == ResultType.SKIPPED:
                LOG.debug("Skipping refresh of %s: %s", app, decision.result.message)
                continue
            self.update_status(context, f"refreshing {app}")
            self.jhelper.charm_refresh(
                app,
                OPENSTACK_MODEL,
                channel=decision.effective_channel,
                revision=decision.effective_revision,
            )
            refreshed_apps.append(app)

        if not refreshed_apps:
            return Result(
                ResultType.COMPLETED, "traefik-k8s applications already up to date"
            )

        try:
            self.update_status(context, "waiting for ingress to stabilise")
            self.jhelper.wait_until_active(
                model=OPENSTACK_MODEL,
                apps=refreshed_apps,
                timeout=1800,
                overlay=build_overlay_dict(refreshed_apps),
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning("Timed out waiting for refreshed ingress: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)


class ReapplyIngressTerraformPlanStep(BaseStep, JujuStepHelper):
    """Reapply only traefik (ingress) components in the openstack Terraform plan.

    Used by `sunbeam cluster refresh ingress`: applies the openstack plan
    with terraform targets discovered from the state (same approach as the
    vault refresh) so only the traefik resources are reconciled.
    """

    _CONFIG = OPENSTACK_TERRAFORM_VARS

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
    ):
        super().__init__(
            "Applying ingress Terraform changes",
            "Applying traefik-k8s-specific Terraform changes",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = OPENSTACK_MODEL

    def _get_ingress_terraform_targets(self) -> list[str]:
        """Get terraform targets for traefik resources.

        Uses terraform state to discover traefik resources that actually
        exist, so optional integrations are only targeted when present
        in state.

        :raises TerraformException: If listing the terraform state fails.
        """
        targets: list[str] = []
        for resource in self.tfhelper.state_list():
            if not (
                resource.startswith("juju_application.")
                or resource.startswith("juju_integration.")
            ):
                continue
            if "traefik" in resource.lower():
                targets.append(f"-target={resource}")

        LOG.debug("Ingress terraform targets: %s", targets)
        return targets

    def run(self, context: StepContext) -> Result:
        """Apply terraform with targets for traefik components only."""
        try:
            targets = self._get_ingress_terraform_targets()
        except TerraformException as e:
            LOG.warning("Error discovering ingress terraform targets: %r", e)
            return Result(ResultType.FAILED, str(e))
        if not targets:
            return Result(
                ResultType.SKIPPED,
                "No traefik resources found in terraform state",
            )

        try:
            self.update_status(context, "updating ingress components")
            LOG.info(
                "Applying terraform with %s ingress-specific targets", len(targets)
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                tf_apply_extra_args=targets,
                reporter=context.reporter,
            )
        except (TerraformException, TerraformStateLockedException) as e:
            LOG.warning("Error updating ingress components: %r", e)
            return Result(ResultType.FAILED, str(e))

        # Wait only for the traefik applications deployed in the model
        apps = self.get_apps_filter_by_charms(self.model, [TRAEFIK_CHARM])
        try:
            self.update_status(context, "waiting for ingress to settle")
            self.jhelper.wait_until_active(
                model=self.model,
                apps=apps,
                timeout=600,
                overlay=build_overlay_dict(apps),
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning("Error waiting for ingress applications: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
