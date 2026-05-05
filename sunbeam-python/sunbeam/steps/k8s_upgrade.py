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
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.manifest import Manifest
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
        self._target_channel: str = K8S_CHANNEL
        self._needs_channel_flag = False

    @property
    def model(self) -> str:
        """Return the model where the k8s charm is deployed."""
        return self.deployment.openstack_machines_model

    def _manifest_channel(self) -> str | None:
        """Return the channel from the manifest, or None if not explicitly set."""
        charm_manifest = self.manifest.find_charm(CHARM_NAME)
        return (charm_manifest.channel if charm_manifest else None) or None

    def _resolve_target_channel(self) -> str:
        """Return the target channel from the manifest, falling back to K8S_CHANNEL."""
        return self._manifest_channel() or K8S_CHANNEL

    def _resolve_target_revision(self) -> int | None:
        """Return the pinned revision from the manifest, or None."""
        charm_manifest = self.manifest.find_charm(CHARM_NAME)
        return charm_manifest.revision if charm_manifest else None

    def is_skip(self, context: StepContext) -> Result:
        """Skip if k8s is not deployed or already at the target revision."""
        try:
            app = self.jhelper.get_application(self.application, self.model)
        except ApplicationNotFoundException:
            return Result(
                ResultType.SKIPPED,
                f"{self.application!r} application has not been deployed yet",
            )

        target_revision = self._resolve_target_revision()
        manifest_channel = self._manifest_channel()
        deployed_channel = app.charm_channel or ""

        # When the user provides only a revision (no channel in the manifest),
        # try to resolve which channel that revision belongs to so we can still
        # enforce the no-track-change rule.
        if target_revision is not None and manifest_channel is None:
            self._needs_channel_flag = False
            if app.charm_rev == target_revision:
                return Result(
                    ResultType.SKIPPED,
                    f"{CHARM_NAME} already at manifest pinned revision"
                    f" {target_revision}",
                )
            # Look up the channel for the requested revision on CharmHub.
            try:
                revision_channel = self.jhelper.get_charm_channel_for_revision(
                    CHARM_NAME, target_revision
                )
            except JujuException as e:
                LOG.debug(
                    "Could not determine channel for revision %s: %s",
                    target_revision,
                    e,
                )
                revision_channel = None

            if revision_channel and deployed_channel:
                deployed_track = deployed_channel.split("/")[0]
                revision_track = revision_channel.split("/")[0]
                if deployed_track != revision_track:
                    return Result(
                        ResultType.FAILED,
                        "k8s upgrade failed: "
                        f"Revision {target_revision} belongs to channel "
                        f"{revision_channel!r} (track {revision_track!r}), "
                        f"but the deployed charm is on track {deployed_track!r}. "
                        "Track changes are not supported by this command.",
                    )
            return Result(ResultType.COMPLETED)

        # When no manifest channel is set, stay on whatever channel is already
        # deployed — this is a patch-only refresh within the same channel/risk.
        # Only switch channel when the manifest explicitly requests one.
        effective_target_channel = manifest_channel or deployed_channel or K8S_CHANNEL

        # Block channel track changes (e.g. 1.32 -> 1.35) only when the manifest
        # explicitly requests a different channel.
        if manifest_channel and deployed_channel:
            deployed_track = deployed_channel.split("/")[0]
            target_track = effective_target_channel.split("/")[0]
            if deployed_track != target_track:
                return Result(
                    ResultType.FAILED,
                    "k8s upgrade failed: "
                    f"Channel track change from {deployed_track!r} to "
                    f"{target_track!r} is not supported by this command. ",
                )

        # _needs_channel_flag: True when the manifest requests a different risk
        # level (e.g. stable -> edge). --channel will be passed to charm_refresh.
        self._needs_channel_flag = deployed_channel != effective_target_channel

        # If a specific revision is pinned together with a channel and already
        # deployed at that exact combination, there is nothing to do.
        if target_revision is not None:
            if (
                deployed_channel == effective_target_channel
                and app.charm_rev == target_revision
            ):
                return Result(
                    ResultType.SKIPPED,
                    f"{CHARM_NAME} already at manifest pinned revision "
                    f"{target_revision} for channel {effective_target_channel}",
                )
            return Result(ResultType.COMPLETED)

        # For risk-level changes, always proceed (no revision check needed).
        if self._needs_channel_flag:
            return Result(ResultType.COMPLETED)

        # Patch upgrade: check whether a newer revision is available.
        try:
            if app.base:
                base = f"{app.base.name}@{app.base.channel}"
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, effective_target_channel, base
                )
            else:
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, effective_target_channel
                )
        except JujuException as e:
            LOG.debug("Could not determine latest revision for %s: %s", CHARM_NAME, e)
            # Can't determine; proceed with the refresh anyway.
            return Result(ResultType.COMPLETED)

        if app.charm_rev == latest_rev:
            return Result(
                ResultType.SKIPPED,
                f"{CHARM_NAME} is already at the latest revision "
                f"{latest_rev} for channel {effective_target_channel}",
            )

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Execute the k8s charm upgrade procedure."""
        target_channel = self._resolve_target_channel()
        target_revision = self._resolve_target_revision()

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
        refresh_channel = target_channel if self._needs_channel_flag else None
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
