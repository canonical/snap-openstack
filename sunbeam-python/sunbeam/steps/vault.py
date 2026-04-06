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
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.features.vault.feature import (
    VaultCommandFailedException,
    VaultHelper,
    auto_unseal_vault,
    migrate_vault_config_in_db,
)
from sunbeam.steps.charm_upgrade import CharmRefreshDecision, check_charm_needs_refresh
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
        self._decision: CharmRefreshDecision

    def upgrade(
        self, revision: int | None = None, channel: str = VAULT_CHANNEL
    ) -> None:
        """Upgrade vault-k8s to the specified channel."""
        LOG.info("Upgrading %s to channel %s", self.application, channel)
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

        Only skips when vault is not deployed or the manifest channel is
        invalid.  The step always runs otherwise so that the DB migration
        and terraform apply happen even when no charm refresh is needed.
        """
        decision = check_charm_needs_refresh(
            self.jhelper,
            self.manifest,
            CHARM_NAME,
            OPENSTACK_MODEL,
            self.application,
            default_channel=VAULT_CHANNEL,
            support_track_upgrades=True,
        )
        if (
            decision.app_not_deployed
            or decision.result.result_type == ResultType.FAILED
        ):
            return decision.result
        self._decision = decision
        # Vault always runs (for DB migration + terraform apply) even when
        # the charm itself is already up-to-date.
        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Run vault-k8s charm upgrade steps."""
        target_channel = self._decision.effective_channel
        revision = self._decision.effective_revision
        skip_charm_refresh = self._decision.result.result_type == ResultType.SKIPPED

        if not skip_charm_refresh:
            try:
                self.update_status(
                    context, f"Refreshing Vault to channel {target_channel}"
                )
                self.upgrade(revision=revision, channel=target_channel)
            except JujuException as e:
                LOG.error("Failed to refresh Vault: %r", e)
                return Result(
                    ResultType.FAILED,
                    f"Failed to refresh Vault: {e}",
                )

            try:
                self.update_status(context, "Waiting for Vault to stabilise")
                # After refresh vault may be blocked on missing config
                # (1.18+ requires pki_ca_common_name) or waiting to be unsealed.
                self.jhelper.wait_until_desired_status(
                    OPENSTACK_MODEL,
                    [self.application],
                    status=["blocked"],
                    timeout=VAULT_UPGRADE_TIMEOUT,
                )
            except (JujuWaitException, TimeoutError) as e:
                LOG.error("Timed out waiting for %s: %r", self.application, e)
                return Result(
                    ResultType.FAILED,
                    f"Timed out waiting for {self.application} to stabilise: {e}",
                )

        try:
            self.update_status(
                context, "Updating terraform plan with new vault channel"
            )
            migrate_vault_config_in_db(self.client, self.tfvar_config, target_channel)
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars={"vault-channel": target_channel},
            )
        except (TerraformException, TerraformStateLockedException) as e:
            return Result(
                ResultType.FAILED,
                f"Failed to apply terraform plan after vault config migration: {e}",
            )

        if skip_charm_refresh:
            return Result(
                ResultType.COMPLETED,
                "Vault config migrated, no charm refresh needed.",
            )

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
                "Timed out waiting for %s to stabilise after terraform apply: %r",
                self.application,
                e,
            )

        try:
            leader_unit = self.jhelper.get_leader_unit(
                self.application, OPENSTACK_MODEL
            )
            vault_status = VaultHelper(self.jhelper).get_vault_status(leader_unit)
        except (JujuException, LeaderNotFoundException, TimeoutError) as e:
            LOG.warning("Could not determine vault seal status: %r", e)
            return Result(
                ResultType.COMPLETED,
                "Vault upgraded. Unable to determine seal status, run "
                "unseal steps if Vault is sealed.",
            )

        if not vault_status.get("sealed"):
            return Result(
                ResultType.FAILED, "Vault is unexpectedly unsealed after upgrade. "
            )

        try:
            # Stop the outer spinner so run_plan's spinners in
            # auto_unseal_vault() display cleanly.
            context.status.stop()
            auto_unseal_vault(self.client, self.jhelper)
        except VaultCommandFailedException as e:
            if "not in dev mode" in str(e):
                return Result(
                    ResultType.COMPLETED,
                    "Vault upgraded. Vault needs to be manually unsealed "
                    "and authorized.",
                )
            return Result(
                ResultType.COMPLETED,
                f"Vault upgraded but auto-unseal failed: {e}. "
                "Run unseal and authorize steps manually.",
            )
        return Result(ResultType.COMPLETED, "Vault upgraded and auto-unsealed.")
