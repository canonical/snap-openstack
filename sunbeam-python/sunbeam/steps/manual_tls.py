# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from sunbeam.clusterd.client import Client
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.steps.charm_upgrade import CharmRefreshDecision, check_charm_needs_refresh
from sunbeam.versions import MANUAL_TLS_CERTIFICATES_CHANNEL

LOG = logging.getLogger(__name__)

CHARM_NAME = "manual-tls-certificates"
MANUAL_TLS_UPGRADE_TIMEOUT = 300


class ManualTLSCharmUpgradeStep(BaseStep, JujuStepHelper):
    """Upgrade the manual-tls-certificates charm to latest channel/revision."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        manifest: Manifest,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        application: str = CHARM_NAME,
    ):
        super().__init__(
            "Upgrade manual-tls-certificates",
            "Upgrading manual-tls-certificates to latest channel/revision",
        )
        self.deployment = deployment
        self.client = client
        self.manifest = manifest
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.application = application
        self.tfvar_config = OPENSTACK_TERRAFORM_VARS
        self._decision: CharmRefreshDecision

    def is_skip(self, context: StepContext) -> Result:
        """Skip if manual-tls-certificates is not deployed or already up-to-date."""
        decision = check_charm_needs_refresh(
            self.jhelper,
            self.manifest,
            CHARM_NAME,
            OPENSTACK_MODEL,
            self.application,
            default_channel=MANUAL_TLS_CERTIFICATES_CHANNEL,
            support_track_upgrades=True,
        )
        self._decision = decision
        if decision.result.result_type == ResultType.FAILED:
            return Result(
                ResultType.FAILED,
                f"manual-tls-certificates upgrade failed: {decision.result.message}",
            )
        return decision.result

    def run(self, context: StepContext) -> Result:
        """Refresh the manual-tls-certificates charm."""
        target_channel = self._decision.effective_channel
        revision = self._decision.effective_revision
        refresh_channel = target_channel if self._decision.needs_channel_flag else None

        try:
            self.update_status(
                context,
                f"Refreshing {CHARM_NAME} to channel {target_channel}"
                + (f" revision {revision}" if revision else ""),
            )
            self.jhelper.charm_refresh(
                self.application,
                OPENSTACK_MODEL,
                channel=refresh_channel,
                revision=revision,
            )
        except JujuException as e:
            LOG.error(f"Failed to refresh {CHARM_NAME}: {e}")
            return Result(
                ResultType.FAILED,
                f"Failed to refresh {CHARM_NAME}: {e}",
            )

        try:
            self.update_status(context, f"Waiting for {CHARM_NAME} to stabilise")
            self.jhelper.wait_until_active(
                OPENSTACK_MODEL,
                apps=[self.application],
                timeout=MANUAL_TLS_UPGRADE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.error(f"Timed out waiting for {self.application}: {e}")
            return Result(
                ResultType.FAILED,
                f"Timed out waiting for {self.application} to stabilise: {e}",
            )

        # Update terraform state with the channel used. Manifest takes
        # precedence over override_tfvars for charm keys.
        try:
            self.update_status(
                context,
                f"Updating terraform plan with new {CHARM_NAME} channel",
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars={
                    "manual-tls-certificates-channel": target_channel,
                },
            )
        except TerraformException as e:
            LOG.warning(
                f"Failed to reapply terraform plan after {CHARM_NAME} upgrade: {e}"
            )

        return Result(
            ResultType.COMPLETED,
            f"{CHARM_NAME} upgraded successfully.",
        )
