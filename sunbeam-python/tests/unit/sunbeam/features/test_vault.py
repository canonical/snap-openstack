# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import MagicMock, Mock

import pytest

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
    def test_is_skip(self):
        vault_status = {"initialized": False}

        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_already_initialized(self):
        vault_status = {"initialized": True}

        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_vault_leader_not_found(self):
        error_message = "Vault leader not found"
        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.side_effect = LeaderNotFoundException(
            error_message
        )

        result = step.is_skip()

        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run(self):
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

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        result.message = json.dumps(vault_cmd_output)

    def test_run_when_vault_command_failed(self):
        error_message = "Vault command execution failed."
        jhelper = Mock()
        step = VaultInitStep(jhelper, 1, 1)
        step.leader_unit = "leader_unit"
        step.vhelper = MagicMock()
        step.vhelper.initialize_vault.side_effect = VaultCommandFailedException(
            error_message
        )

        result = step.run()

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
        self, vault_unseal_status, expected_message
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

        result = step.run()

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
        self, vault_unseal_status_per_unit, expected_message
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

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert result.message == expected_message

    def test_run_single_vault_unit(self):
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

        result = step.run()

        assert result.result_type == ResultType.COMPLETED
        assert result.message == expected_message


class TestAuthorizeVaultCharmStep:
    def test_run(self):
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

        result = step.run()

        jhelper.get_secret_by_name.assert_called_once()
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once()
        jhelper.remove_secret.assert_called_once()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_when_leader_not_found(self):
        error_message = "Vault leader not found"
        token = "fake_root_token"

        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(error_message)

        step = AuthorizeVaultCharmStep(jhelper, token)

        result = step.run()

        jhelper.get_secret_by_name.assert_not_called()
        jhelper.add_secret.assert_not_called()
        jhelper.grant_secret.assert_not_called()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_vault_command_failed(self):
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

        result = step.run()

        jhelper.get_secret_by_name.assert_not_called()
        jhelper.add_secret.assert_not_called()
        jhelper.grant_secret.assert_not_called()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_not_called()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_action_command_failed(self):
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

        result = step.run()

        jhelper.get_secret_by_name.assert_called_once()
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once()
        jhelper.remove_secret.assert_not_called()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == error_message

    def test_run_when_secret_already_exists(self):
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

        result = step.run()

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

    def test_run(self):
        vault_status = {"initialized": True, "sealed": False}
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultStatusStep(jhelper)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.return_value = vault_status

        result = step.run()

        expected_vault_status = {}
        for unit in self.units:
            expected_vault_status[unit] = vault_status
        assert result.result_type == ResultType.COMPLETED
        assert result.message == json.dumps(expected_vault_status)

    def test_run_timedout(self):
        error_message = "timed out"
        self._set_mock_units()

        jhelper = Mock()
        jhelper.get_application.return_value = MagicMock(units=self.units)

        step = VaultStatusStep(jhelper)
        step.vhelper = MagicMock()
        step.vhelper.get_vault_status.side_effect = TimeoutError(error_message)

        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert result.message == error_message
