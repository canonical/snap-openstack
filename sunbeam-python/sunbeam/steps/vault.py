# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    StepContext,
    read_config,
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
from sunbeam.core.manifest import CharmManifest, Manifest
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException, TerraformHelper
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.features.vault.feature import (
    VAULT_DEV_MODE_KEY,
    AuthorizeVaultCharmStep,
    VaultHelper,
    VaultUnsealStep,
)
from sunbeam.versions import VAULT_CHANNEL

LOG = logging.getLogger(__name__)

CHARM_NAME = "vault-k8s"
CHARM_BASE = "ubuntu@24.04"
VAULT_UPGRADE_TIMEOUT = 600


class VaultCharmUpgradeStep(BaseStep, JujuStepHelper):
    """Refresh the vault-k8s charm and unseal if running in dev mode."""

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        manifest: Manifest,
        jhelper: JujuHelper,
        tfhelper: TerraformHelper,
        application: str = "vault",
    ):
        super().__init__(
            "Refresh vault",
            "Refreshing vault-k8s charm",
        )
        self.deployment = deployment
        self.client = client
        self.manifest = manifest
        self.jhelper = jhelper
        self.tfhelper = tfhelper
        self.application = application
        self.tfvar_config = OPENSTACK_TERRAFORM_VARS

    def _charm_manifest(self) -> CharmManifest | None:
        """Return the CharmManifest entry for vault-k8s, or None."""
        charm_manifest = self.manifest.core.software.charms.get(CHARM_NAME)
        if charm_manifest:
            return charm_manifest
        for _, feature in self.manifest.get_features():
            charm_manifest = feature.software.charms.get(CHARM_NAME)
            if charm_manifest:
                return charm_manifest
        return None

    def upgrade(
        self, revision: int | None = None, channel: str = VAULT_CHANNEL
    ) -> None:
        """Upgrade vault-k8s to the specified channel."""
        LOG.info(f"Upgrading {self.application} to channel {channel}")
        self.jhelper.charm_refresh(
            self.application,
            OPENSTACK_MODEL,
            channel=channel,
            revision=revision,
            base=CHARM_BASE,
            trust=True,
        )

    def is_skip(self, context: StepContext) -> Result:
        """Determines if the step should be skipped or not.

        Skips when:
        - application is not deployed
        - channel pinned in manifest has track below VAULT_CHANNEL
        - manifest pins a revision that matches the deployed charm
        - no revision pinned and deployed charm is already at the latest
          revision for the target channel

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            app = self.jhelper.get_application(self.application, OPENSTACK_MODEL)
        except ApplicationNotFoundException:
            return Result(
                ResultType.SKIPPED,
                f"{self.application} application has not been deployed yet",
            )

        charm_manifest = self._charm_manifest()
        target_channel = (
            charm_manifest.channel if charm_manifest else None
        ) or VAULT_CHANNEL
        deployed_channel = app.charm_channel or ""

        if charm_manifest and charm_manifest.channel:
            target_track = target_channel.split("/")[0]
            minimum_vault_track = VAULT_CHANNEL.split("/")[0]
            # Skip if the manifest pins a channel whose track is below the
            # minimum supported channel.
            if target_track != minimum_vault_track and not self.channel_update_needed(
                VAULT_CHANNEL, target_channel
            ):
                msg = (
                    f"Manifest channel {target_channel} track is below "
                    f"{VAULT_CHANNEL}. "
                    "Can not refresh to this channel."
                )
                LOG.warning(msg)
                return Result(ResultType.FAILED, msg)

        if charm_manifest and charm_manifest.revision:
            if (
                deployed_channel == target_channel
                and app.charm_rev == charm_manifest.revision
            ):
                msg = (
                    f"{CHARM_NAME} already at manifest pinned revision "
                    f"{charm_manifest.revision} for channel {target_channel}"
                )
                LOG.debug(msg)
                return Result(ResultType.SKIPPED, msg)
            # Proceed with upgrade if revision pinned in manifest differs from deployed.
            return Result(ResultType.COMPLETED)

        # No revision pinned: Skip if deployed charm is already at the latest revision
        # for the target channel.
        try:
            if app.base:
                base = f"{app.base.name}@{app.base.channel}"
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, target_channel, base, arch="amd64"
                )
            else:
                latest_rev = self.jhelper.get_available_charm_revision(
                    CHARM_NAME, target_channel, arch="amd64"
                )
        except JujuException as e:
            LOG.debug("Could not determine latest revision for %s: %s", CHARM_NAME, e)
            # Proceed with refresh if we cannot confirm the revision.
            return Result(ResultType.COMPLETED)

        if deployed_channel == target_channel and app.charm_rev == latest_rev:
            return Result(
                ResultType.SKIPPED,
                f"{CHARM_NAME} is already at the latest revision for "
                f"channel {target_channel}",
            )
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run vault-k8s charm upgrade steps."""
        charm_manifest = self._charm_manifest()
        revision = charm_manifest.revision if charm_manifest else None
        target_channel = (
            charm_manifest.channel if charm_manifest else None
        ) or VAULT_CHANNEL

        try:
            self.update_status(context, f"Refreshing Vault to channel {target_channel}")
            self.upgrade(revision=revision, channel=target_channel)
        except JujuException as e:
            LOG.error(f"Failed to refresh Vault: {e}")
            return Result(
                ResultType.FAILED,
                f"Failed to refresh Vault: {e}",
            )

        try:
            self.update_status(context, "Waiting for Vault to stabilise")
            # Vault expected to be blocked and unsealed after refresh
            self.jhelper.wait_until_desired_status(
                OPENSTACK_MODEL,
                [self.application],
                status=["blocked"],
                workload_status_message=["Please unseal Vault"],
                timeout=VAULT_UPGRADE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.error(f"Timed out waiting for {self.application}: {e}")
            return Result(
                ResultType.FAILED,
                f"Timed out waiting for {self.application} to stabilise: {e}",
            )

        try:
            self.update_status(
                context, "Updating terraform plan with new vault channel"
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars={"vault-channel": target_channel},
            )
        except TerraformException as e:
            LOG.warning(f"Failed to reapply terraform plan after vault upgrade: {e}")

        try:
            self.update_status(
                context, "Waiting for Vault to settle after terraform apply"
            )
            self.jhelper.wait_until_desired_status(
                OPENSTACK_MODEL,
                [self.application],
                status=["blocked"],
                workload_status_message=["Please unseal Vault"],
                timeout=VAULT_UPGRADE_TIMEOUT,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning(
                f"Timed out waiting for {self.application} to stabilise "
                f"after terraform apply: {e}"
            )

        try:
            leader_unit = self.jhelper.get_leader_unit(
                self.application, OPENSTACK_MODEL
            )
            vault_status = VaultHelper(self.jhelper).get_vault_status(leader_unit)
        except (JujuException, LeaderNotFoundException, TimeoutError) as e:
            LOG.warning(f"Could not determine vault seal status: {e}")
            return Result(
                ResultType.COMPLETED,
                "Vault upgraded. Unable to determine seal status, run "
                "unseal steps if Vault is sealed.",
            )

        if not vault_status.get("sealed"):
            return Result(
                ResultType.FAILED,
                "Vault is unexpectedly unsealed after upgrade.",
            )

        try:
            vault_info = read_config(self.client, VAULT_DEV_MODE_KEY)
        except ConfigItemNotFoundException:
            vault_info = {}

        if vault_info.get("dev_mode"):
            self.update_status(context, "Auto-unsealing vault (dev mode)")
            unseal_keys = vault_info.get("unseal_keys", [])
            root_token = vault_info.get("root_token")
            warnings: list[str] = []

            for key in unseal_keys:
                result = VaultUnsealStep(self.jhelper, key).run(context)
                if result.result_type == ResultType.FAILED:
                    LOG.warning(f"Unseal step failed: {result.message}")
                    warnings.append(result.message)

            if root_token:
                result = AuthorizeVaultCharmStep(self.jhelper, root_token).run(context)
                if result.result_type == ResultType.FAILED:
                    LOG.warning(f"Authorize charm step failed: {result.message}")
                    warnings.append(result.message)

            if warnings:
                return Result(
                    ResultType.COMPLETED,
                    "Vault upgraded but some auto-unseal steps failed: "
                    + "; ".join(warnings),
                )
            return Result(ResultType.COMPLETED, "Vault upgraded and auto-unsealed.")

        return Result(
            ResultType.COMPLETED,
            "Vault upgraded. Vault needs to be manually unsealed and authorized.",
        )
