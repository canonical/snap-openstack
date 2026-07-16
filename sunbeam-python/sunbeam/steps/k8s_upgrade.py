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
    LeaderNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.steps.charm_upgrade import CharmRefreshDecision, check_charm_needs_refresh
from sunbeam.versions import K8S_CHANNEL

LOG = logging.getLogger(__name__)

CHARM_NAME = "k8s"
K8S_UPGRADE_TIMEOUT = 3600  # 1 hour — k8s snap is updated unit-by-unit


class K8SCharmUpgradeStep(BaseStep, JujuStepHelper):
    """Upgrade the k8s charm following the Canonical Kubernetes upgrade procedure.

    Supports patch upgrades and risk-level changes within the same channel track.
    Track changes (e.g. 1.32 -> 1.35) are not supported and will return FAILED.

    Patch (same channel, new revision):
        1. juju run k8s/leader pre-upgrade-check
        2. juju refresh k8s
        3. Wait for k8s to become active

    Risk change (same track, different risk, e.g. 1.32/stable -> 1.32/edge):
        1. juju run k8s/leader pre-upgrade-check
        2. juju refresh k8s --channel <new-channel>
        3. Wait for k8s to become active

    References:
        https://documentation.ubuntu.com/canonical-kubernetes/latest/charm/howto/upgrade-patch/
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        manifest: Manifest,
        jhelper: JujuHelper,
        application: str = CHARM_NAME,
    ):
        super().__init__(
            "Refresh k8s",
            "Refreshing k8s charm",
        )
        self.deployment = deployment
        self.client = client
        self.manifest = manifest
        self.jhelper = jhelper
        self.application = application
        self._decision: CharmRefreshDecision  # set by is_skip()

    @property
    def model(self) -> str:
        """Return the model where the k8s charm is deployed."""
        return self.deployment.openstack_machines_model

    def is_skip(self, context: StepContext) -> Result:
        """Skip if k8s is not deployed or already at the target revision."""
        decision = check_charm_needs_refresh(
            self.jhelper,
            self.manifest,
            CHARM_NAME,
            self.model,
            self.application,
            default_channel=K8S_CHANNEL,
            support_track_upgrades=False,
        )
        self._decision = decision
        if decision.result.result_type == ResultType.FAILED:
            return Result(
                ResultType.FAILED,
                f"k8s upgrade failed: {decision.result.message}",
            )
        return decision.result

    def run(self, context: StepContext) -> Result:
        """Execute the k8s charm upgrade procedure."""
        target_channel = self._decision.effective_channel
        target_revision = self._decision.effective_revision

        # Step 1: pre-upgrade-check
        try:
            leader = self.jhelper.get_leader_unit(self.application, self.model)
        except LeaderNotFoundException as e:
            return Result(
                ResultType.FAILED,
                "k8s upgrade failed: "
                f"Unable to determine leader unit for {self.application!r}: {e}\n"
                f"Check application status with `juju status -m {self.model} k8s` "
                "and re-run `sunbeam cluster refresh k8s` to retry.",
            )

        try:
            self.update_status(
                context,
                f"Running pre-upgrade-check on {leader}",
            )
            self.jhelper.run_action(leader, self.model, "pre-upgrade-check")
        except JujuException as e:
            return Result(
                ResultType.FAILED,
                "k8s upgrade failed: "
                f"pre-upgrade-check failed on {leader}: {e}\n"
                f"Check cluster readiness with `juju status -m {self.model} k8s` "
                "and resolve any issues before re-running "
                "`sunbeam cluster refresh k8s` to retry.",
            )

        # Step 2: juju refresh k8s [--channel CHANNEL]
        # Pass --channel only when the risk level differs from the deployed channel.
        refresh_channel = target_channel if self._decision.needs_channel_flag else None
        LOG.info(
            "Refreshing %s charm%s",
            self.application,
            f" to channel {target_channel}" if refresh_channel else "",
        )
        try:
            self.update_status(
                context,
                f"Refreshing {self.application} charm"
                + (f" to channel {target_channel}" if refresh_channel else ""),
            )
            self.jhelper.charm_refresh(
                self.application,
                self.model,
                channel=refresh_channel,
                revision=target_revision,
            )
        except JujuException as e:
            return Result(
                ResultType.FAILED,
                "k8s upgrade failed: "
                f"Failed to refresh {self.application!r}: {e}\n"
                f"Check application status with `juju status -m {self.model} k8s` "
                "and re-run `sunbeam cluster refresh k8s` to retry.",
            )

        # Step 3: Wait for k8s to return to active.
        # During the upgrade units cycle through maintenance; allow that status
        # as well as active so the wait does not terminate prematurely.
        try:
            self.update_status(
                context,
                f"Waiting for {self.application} to complete upgrade",
            )
            self.jhelper.wait_until_active(
                self.model,
                apps=[self.application],
                timeout=K8S_UPGRADE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            return Result(
                ResultType.FAILED,
                "k8s upgrade failed: "
                f"Timed out waiting for {self.application!r} to become active "
                f"after refresh: {e}\n"
                f"Monitor unit progress with "
                f"`juju status -m {self.model} k8s --watch 5s` "
                "and re-run `sunbeam cluster refresh k8s` once the cluster is healthy.",
            )

        return Result(ResultType.COMPLETED, "k8s charm refreshed.")
