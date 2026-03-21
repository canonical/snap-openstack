# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.core.common import BaseStep, Result, ResultType
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
    JujuHelper,
    JujuStepHelper,
)
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.versions import MANUAL_CERT_AUTH_CHANNEL

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

    def _charm_manifest(self) -> CharmManifest | None:
        """Return the CharmManifest entry for manual-tls-certificates, or None."""
        charm_manifest = self.manifest.core.software.charms.get(CHARM_NAME)
        if charm_manifest:
            return charm_manifest
        for _, feature in self.manifest.get_features():
            charm_manifest = feature.software.charms.get(CHARM_NAME)
            if charm_manifest:
                return charm_manifest
        return None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determine whether this step should be skipped.

        Skips when:
        - The application is not deployed.
        - A revision is pinned in the manifest and already matches what is
          deployed (nothing to change).
        - No revision is pinned and the deployed revision is already the latest
          available on the manifest channel.
        """
        try:
            app = self.jhelper.get_application(self.application, OPENSTACK_MODEL)
        except ApplicationNotFoundException:
            return Result(
                ResultType.SKIPPED,
                f"{self.application} application has not been deployed yet",
            )

        charm_manifest = self._charm_manifest()
        deployed_rev = app.charm_rev

        # If a specific revision is pinned in the manifest, skip only when the
        # deployed revision already matches — otherwise we need to refresh.
        if charm_manifest and charm_manifest.revision is not None:
            if deployed_rev == charm_manifest.revision:
                msg = f"{CHARM_NAME} already at manifest-pinned revision {deployed_rev}"
                LOG.debug(msg)
                return Result(ResultType.SKIPPED, msg)
            return Result(ResultType.COMPLETED)

        manifest_channel = (
            charm_manifest.channel if charm_manifest else None
        ) or MANUAL_CERT_AUTH_CHANNEL

        # No revision pinned — check whether a newer revision is available on
        # the manifest channel (covers both same-channel and cross-channel).
        try:
            if app.base:
                base = f"{app.base.name}@{app.base.channel}"
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, manifest_channel, base, arch="amd64"
                )
            else:
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, manifest_channel, arch="amd64"
                )
        except JujuException as e:
            LOG.debug(f"Could not determine latest revision for {CHARM_NAME}: {e}")
            # Proceed with refresh if we cannot confirm the revision.
            return Result(ResultType.COMPLETED)

        deployed_channel = app.charm_channel or ""
        if deployed_channel == manifest_channel and deployed_rev == latest_rev:
            msg = (
                f"{CHARM_NAME} already on channel {manifest_channel} "
                f"at latest revision {deployed_rev}"
            )
            LOG.debug(msg)
            return Result(ResultType.SKIPPED, msg)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Refresh the manual-tls-certificates charm."""
        charm_manifest = self._charm_manifest()
        channel = (
            charm_manifest.channel if charm_manifest else None
        ) or MANUAL_CERT_AUTH_CHANNEL
        revision = charm_manifest.revision if charm_manifest else None

        # Snapshot workload status before refresh so we can accept either
        # the prior state or active after the refresh completes.
        pre_status: dict[str, str] = {}
        try:
            pre_status = self.jhelper.snapshot_workload_status(
                OPENSTACK_MODEL, [self.application]
            )
        except Exception:
            LOG.debug("Could not snapshot pre-refresh status", exc_info=True)
        LOG.debug(f"Pre-refresh workload status: {pre_status}")

        try:
            self.update_status(
                status,
                f"Refreshing {CHARM_NAME} to channel {channel}"
                + (f" revision {revision}" if revision else ""),
            )
            self.jhelper.charm_refresh(
                self.application,
                OPENSTACK_MODEL,
                channel=channel,
                revision=revision,
            )
        except JujuException as e:
            LOG.error(f"Failed to refresh {CHARM_NAME}: {e}")
            return Result(
                ResultType.FAILED,
                f"Failed to refresh {CHARM_NAME}: {e}",
            )

        try:
            self.update_status(status, f"Waiting for {CHARM_NAME} to stabilise")
            prior = pre_status.get(self.application, "active")
            accepted = list({prior, "active"})
            self.jhelper.wait_application_ready(
                self.application,
                OPENSTACK_MODEL,
                accepted_status=accepted,
                timeout=MANUAL_TLS_UPGRADE_TIMEOUT,
            )
        except TimeoutError as e:
            LOG.error(f"Timed out waiting for {self.application}: {e}")
            return Result(
                ResultType.FAILED,
                f"Timed out waiting for {self.application} to stabilise: {e}",
            )

        # Update terraform state with the channel used. If the manifest already
        # pins a revision, update_tfvars_and_apply_tf will honour it (manifest
        # takes precedence over override_tfvars for charm keys).
        try:
            self.update_status(
                status,
                f"Updating terraform plan with new {CHARM_NAME} channel",
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars={
                    "manual-tls-certificates-channel": channel,
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
