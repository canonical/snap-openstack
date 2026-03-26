# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import Result, ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuException,
)
from sunbeam.steps.vault import (
    CHARM_BASE,
    VAULT_CHANNEL,
    VaultCharmUpgradeStep,
)


@pytest.fixture
def vaulthelper():
    with patch("sunbeam.steps.vault.VaultHelper") as p:
        yield p.return_value


@pytest.fixture
def mock_read_config():
    with patch("sunbeam.steps.vault.read_config") as p:
        yield p


@pytest.fixture
def vault_unseal_step():
    with patch("sunbeam.steps.vault.VaultUnsealStep") as p:
        yield p


@pytest.fixture
def authorize_vault_step():
    with patch("sunbeam.steps.vault.AuthorizeVaultCharmStep") as p:
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

    def test_skip_when_manifest_channel_and_revision_match_deployment(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=200, charm_channel="1.19/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_manifest.find_charm.return_value = Mock(
            channel="1.19/stable", revision=200
        )

        with patch.object(step, "channel_update_needed", return_value=True):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "already at manifest pinned" in result.message

    def test_skip_when_already_at_latest_revision_for_target(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=100, charm_channel=VAULT_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 100
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        assert "latest revision" in result.message

    def test_not_skipped_when_newer_revision_available(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        app = Mock(charm_rev=50, charm_channel="1.18/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 100
        basic_manifest.find_charm.return_value = None

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

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
        self, step, basic_jhelper, basic_manifest, vaulthelper, step_context
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
        mock_read_config,
        step_context,
    ):
        basic_manifest.find_charm.return_value = Mock(
            channel="1.18/stable", revision=None
        )
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        mock_read_config.return_value = {"dev_mode": False}

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

    def test_run_vault_sealed_no_dev_mode_config(
        self, step, basic_jhelper, vaulthelper, mock_read_config, step_context
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_read_config.side_effect = ConfigItemNotFoundException()

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "manually unsealed" in result.message

    def test_run_vault_sealed_dev_mode_false(
        self, step, basic_jhelper, vaulthelper, mock_read_config, step_context
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_read_config.return_value = {"dev_mode": False}

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "manually unsealed" in result.message

    def test_run_vault_sealed_dev_mode_auto_unseal(
        self,
        step,
        basic_jhelper,
        vaulthelper,
        mock_read_config,
        vault_unseal_step,
        authorize_vault_step,
        step_context,
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"

        vault_info = {
            "dev_mode": True,
            "unseal_keys": ["key1", "key2"],
            "root_token": "s.roottoken",
        }

        mock_read_config.return_value = vault_info
        vaulthelper.get_vault_status.return_value = {"sealed": True}

        mock_unseal_instance = Mock()
        mock_unseal_instance.run.return_value = Result(ResultType.COMPLETED)
        vault_unseal_step.return_value = mock_unseal_instance

        mock_authorize_instance = Mock()
        mock_authorize_instance.run.return_value = Result(ResultType.COMPLETED)
        authorize_vault_step.return_value = mock_authorize_instance

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert "auto-unsealed" in result.message

        assert vault_unseal_step.call_count == 2
        vault_unseal_step.assert_any_call(basic_jhelper, "key1")
        vault_unseal_step.assert_any_call(basic_jhelper, "key2")

        authorize_vault_step.assert_called_once_with(basic_jhelper, "s.roottoken")
