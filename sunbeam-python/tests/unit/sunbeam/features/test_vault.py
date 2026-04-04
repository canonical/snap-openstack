# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    JujuSecretNotFound,
    LeaderNotFoundException,
)
from sunbeam.features.vault.feature import (
    AuthorizeVaultCharmStep,
    VaultCommandFailedException,
    VaultHelper,
    VaultInitStep,
    VaultStatusStep,
    VaultUnsealStep,
    migrate_vault_config_in_db,
    vault_pki_config_key,
)


class TestVaultHelper:
    def test_get_vault_status(self):
        unit = "leader-unit"
        command_result = {}
        vault_command_output = {"return-code": 0, "stdout": json.dumps(command_result)}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        result = vhelper.get_vault_status(unit)
        assert result == command_result

    def test_initialize_vault(self):
        unit = "leader-unit"
        command_result = {}
        vault_command_output = {"return-code": 0, "stdout": json.dumps(command_result)}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        result = vhelper.initialize_vault(unit, 1, 1)
        assert result == {}

    def test_initialize_vault_returns_nonzero_code(self):
        unit = "leader-unit"
        error_message = "Vault already initialized"
        vault_command_output = {"return-code": 1, "stderr": error_message}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        with pytest.raises(VaultCommandFailedException) as e:
            vhelper.initialize_vault(unit, 1, 1)

        assert str(e.value) == error_message

    def test_unseal_vault(self):
        unit = "leader-unit"
        unseal_key = "fake-unseal-key"

        command_result = {}
        vault_command_output = {"return-code": 0, "stdout": json.dumps(command_result)}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        result = vhelper.unseal_vault(unit, unseal_key)
        assert result == {}

    def test_unseal_vault_returns_nonzero_code(self):
        unit = "leader-unit"
        unseal_key = "fake-unseal-key"

        error_message = "Vault is not initialized"
        vault_command_output = {"return-code": 1, "stderr": error_message}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        with pytest.raises(VaultCommandFailedException) as e:
            vhelper.unseal_vault(unit, unseal_key)

        assert str(e.value) == error_message

    def test_create_token(self):
        unit = "leader-unit"
        root_token = "fake-root-token"

        command_result = {}
        vault_command_output = {"return-code": 0, "stdout": json.dumps(command_result)}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        result = vhelper.create_token(unit, root_token)
        assert result == {}

    def test_create_token_returns_nonzero_code(self):
        unit = "leader-unit"
        root_token = "fake-root-token"

        error_message = "Vault is not initialized"
        vault_command_output = {"return-code": 1, "stderr": error_message}

        jhelper = Mock()
        jhelper.run_cmd_on_unit_payload.return_value = vault_command_output
        vhelper = VaultHelper(jhelper)

        with pytest.raises(VaultCommandFailedException) as e:
            vhelper.create_token(unit, root_token)

        assert str(e.value) == error_message


class TestVaultInitStep:
    def test_is_skip(self, step_context):
        vault_status = {"initialized": False}

        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_already_initialized(self, step_context):
        vault_status = {"initialized": True}

        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_vault_leader_not_found(self, step_context):
        error_message = "Vault leader not found"
        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.side_effect = LeaderNotFoundException(
            error_message
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run(self, step_context):
        vault_cmd_output = {
            "unseal_keys_b64": [
                "fake-unseal-key-1",
            ],
            "unseal_keys_hex": ["fake-unseal-key-1"],
            "unseal_shares": 1,
            "unseal_threshold": 1,
            "recovery_keys_b64": [],
            "recovery_keys_hex": [],
            "recovery_keys_shares": 0,
            "recovery_keys_threshold": 0,
            "root_token": "fake-root-token",
        }

        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.leader_unit = "leader_unit"
        step.vhelper = MagicMock()
        step.vhelper.initialize_vault.return_value = vault_cmd_output

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        result.message = json.dumps(vault_cmd_output)

    def test_run_when_vault_command_failed(self, step_context):
        error_message = "Vault command execution failed."
        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.leader_unit = "leader_unit"
        step.vhelper = MagicMock()
        step.vhelper.initialize_vault.side_effect = VaultCommandFailedException(
            error_message
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == error_message


class TestVaultUnsealStep:
    def _set_mock_units(self):
        self.units = {
            "vault/0": MagicMock(),
            "vault/1": MagicMock(),
            "vault/2": MagicMock(),
        }

    @pytest.mark.parametrize(
        "vault_unseal_status, expected_message",
        [
            # Unseal with the first key out of 3
            (
                {"sealed": True, "t": 3, "progress": 1},
                "Vault unseal operation status: 2 key shares required to unseal",
            ),
            # Unseal with second key out of 3
            (
                {"sealed": True, "t": 3, "progress": 2},
                "Vault unseal operation status: 1 key shares required to unseal",
            ),
            # Unseal with final key out of 3
            (
                {"sealed": False, "t": 3, "progress": 0},
                (
                    "Vault unseal operation status: completed for leader unit."
                    "\nRerun `sunbeam vault unseal` command to unseal non-leader units."
                ),
            ),
        ],
    )
    def test_run_when_leader_unit_is_sealed(
        self,
        vault_unseal_status,
        expected_message,
        step_context,
    ):
        unseal_key = "fake-unseal-key"
        vault_status = {"sealed": True}
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultUnsealStep(jhelper, unseal_key)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status
        step.vhelper.unseal_vault.return_value = vault_unseal_status

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message == expected_message

    @pytest.mark.parametrize(
        "vault_unseal_status_per_unit, expected_message",
        [
            # Unseal with the first key out of 3
            (
                [
                    {"sealed": True, "t": 3, "progress": 1},
                    {"sealed": True, "t": 3, "progress": 1},
                ],
                (
                    "Vault unseal operation status: "
                    "\nvault/1 : 2 key shares required to unseal"
                    "\nvault/2 : 2 key shares required to unseal"
                ),
            ),
            # Unseal with second key out of 3
            (
                [
                    {"sealed": True, "t": 3, "progress": 2},
                    {"sealed": True, "t": 3, "progress": 2},
                ],
                (
                    "Vault unseal operation status: "
                    "\nvault/1 : 1 key shares required to unseal"
                    "\nvault/2 : 1 key shares required to unseal"
                ),
            ),
            # Unseal with final key out of 3
            (
                [
                    {"sealed": True, "t": 3, "progress": 0},
                    {"sealed": True, "t": 3, "progress": 0},
                ],
                ("Vault unseal operation status: completed"),
            ),
            # Unseal with mix of shares remaning
            (
                [
                    {"sealed": True, "t": 3, "progress": 1},
                    {"sealed": True, "t": 3, "progress": 2},
                ],
                (
                    "Vault unseal operation status: "
                    "\nvault/1 : 2 key shares required to unseal"
                    "\nvault/2 : 1 key shares required to unseal"
                ),
            ),
            # One non-leader-unit already unsealed
            (
                [
                    {"sealed": False, "t": 3, "progress": 0},
                    {"sealed": True, "t": 3, "progress": 0},
                ],
                ("Vault unseal operation status: completed"),
            ),
            # All units are already unsealed
            (
                [
                    {"sealed": False, "t": 3, "progress": 0},
                    {"sealed": False, "t": 3, "progress": 0},
                ],
                ("Vault unseal operation status: completed"),
            ),
        ],
    )
    def test_run_when_leader_unit_is_unsealed(
        self,
        vault_unseal_status_per_unit,
        expected_message,
        step_context,
    ):
        unseal_key = "fake-unseal-key"
        vault_leader_status = {"sealed": False}
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultUnsealStep(jhelper, unseal_key)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_leader_status
        step.vhelper.unseal_vault.side_effect = vault_unseal_status_per_unit

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message == expected_message

    def test_run_single_vault_unit(self, step_context):
        unseal_key = "fake-unseal-key"
        vault_status = {"sealed": True}
        self._set_mock_units()
        self.units = {"vault/0": MagicMock()}

        vault_unseal_status = {"sealed": False, "t": 3, "progress": 0}
        expected_message = "Vault unseal operation status: completed"

        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultUnsealStep(jhelper, unseal_key)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status
        step.vhelper.unseal_vault.return_value = vault_unseal_status

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message == expected_message


class TestAuthorizeVaultCharmStep:
    def test_run(self, step_context):
        token = "fake_root_token"
        vault_cmd_output = {"auth": {"client_token": "fake_token"}}

        jhelper = Mock()
        jhelper.get_secret_by_name.side_effect = JujuSecretNotFound()
        jhelper.add_secret.return_vaule = "secret:fakesecret"
        jhelper.grant_secret.return_value = True
        jhelper.remove_secret.return_value = True
        jhelper.run_action.return_value = {"return-code": 0}

        step = AuthorizeVaultCharmStep(jhelper, token)
        step.vhelper = MagicMock()
        step.vhelper.create_token.return_value = vault_cmd_output

        result = step.run(step_context)

        jhelper.get_secret_by_name.assert_called_once()
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once()
        jhelper.remove_secret.assert_called_once()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_leader_not_found(self, step_context):
        error_message = "Vault leader not found"
        token = "fake_root_token"

        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(error_message)

        step = AuthorizeVaultCharmStep(jhelper, token)

        result = step.run(step_context)

        jhelper.get_secret_by_name.assert_not_called()
        jhelper.add_secret.assert_not_called()
        jhelper.grant_secret.assert_not_called()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_vault_command_failed(self, step_context):
        error_message = "Vault command execution failed."
        token = "fake_root_token"
        # vault_cmd_output = {"auth": {"client_token": "fake_token"}}

        jhelper = Mock()
        """
        jhelper.get_secret_by_name.side_effect = JujuSecretNotFound()
        jhelper.add_secret.return_vaule = "secret:fakesecret"
        jhelper.grant_secret.return_value = True
        jhelper.remove_secret.return_value = True
        jhelper.run_action.return_value = {"return-code": 0}
        """

        step = AuthorizeVaultCharmStep(jhelper, token)
        step.vhelper = MagicMock()
        step.vhelper.create_token.side_effect = VaultCommandFailedException(
            error_message
        )

        result = step.run(step_context)

        jhelper.get_secret_by_name.assert_not_called()
        jhelper.add_secret.assert_not_called()
        jhelper.grant_secret.assert_not_called()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_action_command_failed(self, step_context):
        error_message = "Vault authorize action failed."
        token = "fake_root_token"
        vault_cmd_output = {"auth": {"client_token": "fake_token"}}

        jhelper = Mock()
        jhelper.get_secret_by_name.side_effect = JujuSecretNotFound()
        jhelper.add_secret.return_vaule = "secret:fakesecret"
        jhelper.grant_secret.return_value = True
        jhelper.remove_secret.return_value = True
        jhelper.run_action.side_effect = ActionFailedException(error_message)

        step = AuthorizeVaultCharmStep(jhelper, token)
        step.vhelper = MagicMock()
        step.vhelper.create_token.return_value = vault_cmd_output

        result = step.run(step_context)

        jhelper.get_secret_by_name.assert_called_once()
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_secret_already_exists(self, step_context):
        token = "fake_root_token"
        vault_cmd_output = {"auth": {"client_token": "fake_token"}}

        jhelper = Mock()
        jhelper.get_secret_by_name.return_value = "secret:fakesecret"
        jhelper.add_secret.return_vaule = "secret:fakesecret"
        jhelper.grant_secret.return_value = True
        jhelper.remove_secret.return_value = True
        jhelper.run_action.return_value = {"return-code": 0}

        step = AuthorizeVaultCharmStep(jhelper, token)
        step.vhelper = MagicMock()
        step.vhelper.create_token.return_value = vault_cmd_output

        result = step.run(step_context)

        jhelper.get_secret_by_name.assert_called_once()
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once()
        jhelper.remove_secret.assert_called()
        jhelper.run_action.assert_called_once()
        # remove secret called twice
        assert jhelper.remove_secret.call_count == 2
        assert result.result_type == ResultType.COMPLETED


class TestVaultStatusStep:
    def _set_mock_units(self):
        self.units = {
            "vault/0": MagicMock(),
            "vault/1": MagicMock(),
            "vault/2": MagicMock(),
        }

    def test_run(self, step_context):
        vault_status = {"initialized": True, "sealed": False}
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultStatusStep(jhelper)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.run(step_context)

        expected_vault_status = {}
        for unit in self.units:
            expected_vault_status[unit] = vault_status
        assert result.result_type == ResultType.COMPLETED
        assert result.message == json.dumps(expected_vault_status)

    def test_run_timedout(self, step_context):
        error_message = "timed out"
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultStatusStep(jhelper)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.side_effect = TimeoutError(error_message)

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert result.message == error_message


class TestVaultPkiConfigKey:
    @pytest.mark.parametrize(
        "channel, expected_key",
        [
            ("1.16/stable", "common_name"),
            ("1.17/stable", "common_name"),
            ("1.17/edge", "common_name"),
            ("1.18/stable", "pki_ca_common_name"),
            ("1.18/edge", "pki_ca_common_name"),
            ("1.19/stable", "pki_ca_common_name"),
            ("2.0/stable", "pki_ca_common_name"),
        ],
    )
    def test_returns_correct_key_for_channel(self, channel, expected_key):
        assert vault_pki_config_key(channel) == expected_key

    def test_unparseable_channel_defaults_to_new_key(self):
        assert vault_pki_config_key("latest/stable") == "pki_ca_common_name"

    def test_empty_channel_defaults_to_new_key(self):
        assert vault_pki_config_key("") == "pki_ca_common_name"


class TestMigrateVaultConfigInDb:
    def test_migrates_common_name_to_pki_ca_common_name(self):
        stored = {
            "_computed_keys": ["vault-config"],
            "vault-config": {"common_name": "example.com"},
            "keystone-channel": "2024.1/stable",
        }
        client = Mock()
        client.cluster.get_config.return_value = json.dumps(stored)

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_called_once()
        saved_data = mock_update.call_args.args[2]
        assert saved_data["vault-config"]["pki_ca_common_name"] == "example.com"
        assert "common_name" not in saved_data["vault-config"]
        assert saved_data["vault-config"]["pki_allow_subdomains"] is True

    def test_migrates_pki_ca_common_name_to_common_name(self):
        stored = {
            "_computed_keys": ["vault-config"],
            "vault-config": {
                "pki_ca_common_name": "example.com",
                "pki_allow_subdomains": True,
            },
        }
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.16/stable"
                )

        mock_update.assert_called_once()
        saved_data = mock_update.call_args.args[2]
        assert saved_data["vault-config"] == {"common_name": "example.com"}
        assert "pki_allow_subdomains" not in saved_data["vault-config"]

    def test_both_keys_present_preserves_new_key(self):
        stored = {
            "vault-config": {
                "common_name": "old.example.com",
                "pki_ca_common_name": "example.com",
                "pki_allow_subdomains": True,
            },
        }
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_called_once()
        saved_data = mock_update.call_args.args[2]
        assert saved_data["vault-config"]["pki_ca_common_name"] == "example.com"
        assert "common_name" not in saved_data["vault-config"]

    def test_noop_when_key_already_correct(self):
        stored = {
            "vault-config": {
                "pki_ca_common_name": "example.com",
                "pki_allow_subdomains": True,
            },
        }
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_not_called()

    def test_adds_pki_allow_subdomains_when_missing_on_118(self):
        stored = {
            "vault-config": {"pki_ca_common_name": "example.com"},
        }
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_called_once()
        saved_data = mock_update.call_args.args[2]
        assert saved_data["vault-config"]["pki_allow_subdomains"] is True

    def test_noop_when_no_vault_config(self):
        stored = {"keystone-channel": "2024.1/stable"}
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_not_called()

    def test_noop_when_config_not_found(self):
        client = Mock()

        with patch(
            "sunbeam.features.vault.feature.read_config",
            side_effect=ConfigItemNotFoundException,
        ):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_not_called()

    def test_noop_when_vault_config_is_none(self):
        stored = {"vault-config": None}
        client = Mock()

        with patch("sunbeam.features.vault.feature.read_config", return_value=stored):
            with patch("sunbeam.features.vault.feature.update_config") as mock_update:
                migrate_vault_config_in_db(
                    client, "TerraformVarsOpenstack", "1.18/stable"
                )

        mock_update.assert_not_called()
