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
    VAULT_UPGRADE_TIMEOUT,
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
    def test_skip_when_application_not_deployed(self, step, basic_jhelper):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert "not been deployed" in result.message

    def test_skip_when_already_on_target_channel(
        self, step, basic_jhelper, basic_manifest
    ):
        app = Mock(charm_rev=100, charm_channel=VAULT_CHANNEL, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 100
        basic_manifest.core.software.charms.get.return_value = None

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED
        assert VAULT_CHANNEL in result.message

    def test_not_skipped_when_on_different_channel(self, step, basic_jhelper):
        app = Mock(charm_channel="1.16/stable")
        basic_jhelper.get_application.return_value = app

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_success_vault_unsealed(
        self, step, basic_jhelper, basic_manifest, vaulthelper
    ):
        basic_manifest.core.software.charms.get.return_value = None
        vaulthelper.get_vault_status.return_value = {"sealed": False}
        basic_jhelper.get_leader_unit.return_value = "vault/0"

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert "unsealed" in result.message
        basic_jhelper.charm_refresh.assert_called_once_with(
            "vault",
            "openstack",
            channel=VAULT_CHANNEL,
            revision=None,
            base=CHARM_BASE,
            trust=True,
        )
        basic_jhelper.wait_until_desired_status.assert_called_once_with(
            "openstack",
            ["vault"],
            status=["blocked", "active"],
            timeout=VAULT_UPGRADE_TIMEOUT,
        )

    def test_run_charm_refresh_fails(self, step, basic_jhelper):
        basic_jhelper.charm_refresh.side_effect = JujuException(
            "something bad happened"
        )

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "something bad happened" in result.message
        basic_jhelper.wait_until_desired_status.assert_not_called()

    def test_run_wait_times_out(self, step, basic_jhelper):
        basic_jhelper.wait_until_desired_status.side_effect = TimeoutError("timed out")

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_run_vault_sealed_no_dev_mode_config(
        self, step, basic_jhelper, vaulthelper, mock_read_config
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_read_config.side_effect = ConfigItemNotFoundException()

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert "manually unsealed" in result.message

    def test_run_vault_sealed_dev_mode_false(
        self, step, basic_jhelper, vaulthelper, mock_read_config
    ):
        basic_jhelper.get_leader_unit.return_value = "vault/0"
        vaulthelper.get_vault_status.return_value = {"sealed": True}
        mock_read_config.return_value = {"dev_mode": False}

        result = step.run()

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

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert "auto-unsealed" in result.message

        assert vault_unseal_step.call_count == 2
        vault_unseal_step.assert_any_call(basic_jhelper, "key1")
        vault_unseal_step.assert_any_call(basic_jhelper, "key2")

        authorize_vault_step.assert_called_once_with(basic_jhelper, "s.roottoken")
