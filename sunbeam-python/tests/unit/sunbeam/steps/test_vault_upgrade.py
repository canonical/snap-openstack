# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
)
from sunbeam.core.terraform import TerraformException
from sunbeam.features.vault.feature import VaultCommandFailedException
from sunbeam.steps.vault import (
    CHARM_BASE,
    VAULT_CHANNEL,
    VaultCharmUpgradeStep,
)


@pytest.fixture
def mock_migrate():
    with patch("sunbeam.steps.vault.migrate_vault_config_in_db") as p:
        yield p


@pytest.fixture
def vaulthelper():
    with patch("sunbeam.steps.vault.VaultHelper") as p:
        yield p.return_value


@pytest.fixture
def mock_auto_unseal():
    with patch("sunbeam.steps.vault.auto_unseal_vault") as p:
        yield p


@pytest.fixture
def step(basic_deployment, basic_client, basic_manifest, basic_jhelper, basic_tfhelper):
    return VaultCharmUpgradeStep(
        basic_deployment, basic_client, basic_manifest, basic_jhelper, basic_tfhelper
    )


class TestVaultCharmUpgradeStep:
    def test_skip_when_application_not_deployed(
        self, step, basic_jhelper, step_context
    ):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "not been deployed" in result.message

    def test_failed_when_manifest_channel_track_below_minimum(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=50, charm_channel="1.16/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.16/stable", revision=None
        )

        with patch.object(step, "channel_update_needed", return_value=False):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED
        assert "track is below" in result.message

    def test_no_skip_when_manifest_channel_and_revision_match_deployment(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=200, charm_channel="1.19/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.19/stable", revision=200
        )

        with patch.object(step, "channel_update_needed", return_value=True):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._skip_charm_refresh is True

    def test_no_skip_when_already_at_latest_revision_for_target(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=100, charm_channel=VAULT_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 100
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._skip_charm_refresh is True

    def test_not_skipped_when_newer_revision_available(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=50, charm_channel="1.18/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 100
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step._skip_charm_refresh is False

    def test_not_skipped_when_manifest_channel_is_upgrade(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=100, charm_channel="1.18/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 120
        basic_manifest.find_charm.return_value = Mock(
            channel="1.19/stable", revision=None
        )

        with patch.object(step, "channel_update_needed", return_value=True):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_available_charm_revision.assert_called_once_with(
            "vault-k8s", "1.19/stable", arch="amd64"
        )

    def test_run_fails_when_vault_unsealed_after_upgrade(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        vaulthelper,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = None
        vaulthelper.get_vault_status.return_value = {"sealed": False}
        basic_jhelper.get_leader_unit.return_value = "vault/0"

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "unexpectedly unsealed" in result.message
        basic_jhelper.charm_refresh.assert_called_once_with(
            "vault",
            "openstack",
            channel=VAULT_CHANNEL,
            revision=None,
            base=CHARM_BASE,
            trust=True,
        )
        assert basic_jhelper.wait_until_desired_status.call_count == 2

    def test_run_uses_manifest_channel(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = Mock(
            channel="1.18/stable", revision=None
        )
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        mock_auto_unseal.return_value = None

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once_with(
            "vault",
            "openstack",
            channel="1.18/stable",
            revision=None,
            base=CHARM_BASE,
            trust=True,
        )
        basic_tfhelper = step.tfhelper
        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        _, kwargs = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        assert kwargs["override_tfvars"]["vault-channel"] == "1.18/stable"

    def test_run_charm_refresh_fails(self, step, basic_jhelper, step_context):
        basic_jhelper.charm_refresh.side_effect = JujuException(
            "something bad happened"
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "something bad happened" in result.message
        basic_jhelper.wait_until_desired_status.assert_not_called()

    def test_run_wait_times_out(self, step, basic_jhelper, step_context):
        basic_jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_run_vault_sealed_no_dev_mode(
        self,
        step,
        basic_jhelper,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_auto_unseal.side_effect = VaultCommandFailedException(
            "Vault is not in dev mode"
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "manually unsealed" in result.message

    def test_run_vault_sealed_dev_mode_auto_unseal(
        self,
        step,
        basic_jhelper,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_auto_unseal.return_value = None

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "auto-unsealed" in result.message
        mock_auto_unseal.assert_called_once_with(step.client, basic_jhelper)

    def test_run_vault_sealed_auto_unseal_fails(
        self,
        step,
        basic_jhelper,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_auto_unseal.side_effect = VaultCommandFailedException("unseal failed")

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "auto-unseal failed" in result.message
        assert "unseal failed" in result.message

    def test_run_skips_unseal_when_charm_refresh_skipped(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        basic_tfhelper,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = None
        step._skip_charm_refresh = True

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "no charm refresh needed" in result.message
        mock_migrate.assert_called_once()
        basic_jhelper.charm_refresh.assert_not_called()
        basic_jhelper.get_leader_unit.assert_not_called()

    def test_run_fails_when_terraform_fails_on_skip_path(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        basic_tfhelper,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = None
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed"
        )
        step._skip_charm_refresh = True

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "terraform" in result.message.lower()

    def test_run_fails_when_terraform_fails_after_refresh(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        basic_tfhelper,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = None
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed"
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "terraform" in result.message.lower()
        basic_jhelper.get_leader_unit.assert_not_called()

    def test_run_calls_migrate_vault_config(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        basic_tfhelper,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = None
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        mock_auto_unseal.return_value = None

        step.run(step_context)

        mock_migrate.assert_called_once_with(
            step.client, step.tfvar_config, VAULT_CHANNEL
        )

    def test_run_calls_migrate_with_manifest_channel(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        basic_tfhelper,
        vaulthelper,
        mock_auto_unseal,
        mock_migrate,
        step_context,
    ):
        basic_manifest.find_charm.return_value = Mock(
            channel="1.19/stable", revision=None
        )
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        mock_auto_unseal.return_value = None

        step.run(step_context)

        mock_migrate.assert_called_once_with(
            step.client, step.tfvar_config, "1.19/stable"
        )
