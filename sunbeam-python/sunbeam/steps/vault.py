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


def _get_vault_terraform_targets(
    tfhelper: TerraformHelper,
) -> list[str]:
    """Get terraform targets for Vault resources.

    Uses terraform state to discover vault resources that actually
    exist, so optional integrations are only targeted when present
    in state.
    """
    targets: list[str] = []

    try:
        state_resources = tfhelper.state_list()
        for resource in state_resources:
            if not (
                resource.startswith("juju_application.")
                or resource.startswith("juju_integration.")
            ):
                continue
            if "vault" in resource.lower():
                targets.append(f"-target={resource}")
    except Exception as e:
        LOG.warning("Error discovering vault terraform targets: %s", e)

    LOG.debug("Vault terraform targets: %s", targets)
    return targets


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

    def _get_vault_terraform_targets(self) -> list[str]:
        """Get terraform targets for Vault resources."""
        return _get_vault_terraform_targets(self.tfhelper)

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

            # Build target list for vault and its integrations
            targets = self._get_vault_terraform_targets()

            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.tfvar_config,
                override_tfvars={"vault-channel": target_channel},
                tf_apply_extra_args=targets,
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


class ReapplyVaultTerraformPlanStep(BaseStep, JujuStepHelper):
    """Reapply only Vault-related components in the Terraform plan."""

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
            "Applying Vault Terraform changes",
            "Applying Vault-specific Terraform changes",
        )
        self.application = "vault"
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = OPENSTACK_MODEL

    def _get_vault_terraform_targets(self) -> list[str]:
        """Get terraform targets for Vault resources."""
        return _get_vault_terraform_targets(self.tfhelper)

    def run(self, context: StepContext) -> Result:
        """Apply terraform with targets for Vault components only."""
        try:
            self.update_status(context, "updating Vault components")

            # Build target list for vault and its integrations
            targets = self._get_vault_terraform_targets()

            # Apply terraform with targets
            LOG.info("Applying terraform with %s Vault-specific targets", len(targets))
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                tf_apply_extra_args=targets,
                reporter=context.reporter,
            )

        except (TerraformException, TerraformStateLockedException) as e:
            LOG.warning("Error updating Vault components: %r", e)
            return Result(ResultType.FAILED, str(e))

        # Wait only for vault application
        try:
            self.update_status(context, "waiting for Vault to settle")
            LOG.debug("Waiting for Vault application")
            self.jhelper.wait_until_active(
                model=self.model,
                apps=[self.application],
                timeout=600,
            )
        except (JujuWaitException, TimeoutError) as e:
            LOG.warning("Error waiting for Vault application: %r", e)
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)
