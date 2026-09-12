# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local encrypted compute-storage step."""

from unittest import mock

import pytest
from rich.console import Console

from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.core.juju import ActionFailedException, JujuException
from sunbeam.provider.local.steps import LocalConfigureEncryptedStorageStep

NODE = "compute-1.example.com"


def make_config():
    return ComputeStorageConfig(
        vault_offer_url="vault.vault-kv",
        nodes={
            NODE: NodeStorageConfig(
                target="/dev/disk/by-id/wwn-0x1",
                existing_key_secret_id="secret:abc",
            )
        },
    )


def make_step(config=None, deployment=None):
    client = mock.MagicMock()
    client.cluster.get_node_info.return_value = {"machineid": "5"}
    jhelper = mock.MagicMock()
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/1"
    jhelper.run_action.return_value = {
        "status": "completed",
        "target": "/dev/disk/by-id/wwn-0x1",
    }
    step = LocalConfigureEncryptedStorageStep(
        client,
        NODE,
        jhelper,
        "machine-model",
        deployment or mock.MagicMock(),
        config=config,
    )
    step.update_status = mock.MagicMock()
    return step, client, jhelper


class TestPrompt:
    def test_prompt_uses_config(self):
        step, _client, _jhelper = make_step(make_config())

        assert step.has_prompts() is True
        step.prompt(Console(), show_hint=False)

        assert step.target == "/dev/disk/by-id/wwn-0x1"
        assert step.existing_key_secret_id == "secret:abc"

    def test_prompt_missing_node_raises(self):
        config = ComputeStorageConfig(vault_offer_url="vault.vault-kv", nodes={})
        step, _client, _jhelper = make_step(config)

        with pytest.raises(SunbeamException):
            step.prompt(Console(), show_hint=False)

    def test_prompt_without_console_does_nothing(self):
        step, _client, _jhelper = make_step(None)

        step.prompt(None, show_hint=False)

        assert step.target is None


class TestIsSkip:
    def test_skip_requires_target(self):
        step, _client, _jhelper = make_step(None)

        result = step.is_skip(mock.MagicMock())

        assert result.result_type == ResultType.FAILED

    def test_not_skipped_with_config(self):
        step, _client, _jhelper = make_step(make_config())

        result = step.is_skip(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED


class TestRun:
    @pytest.fixture(autouse=True)
    def no_guests(self):
        """Default the "no instances remain" guard to pass for every test."""
        with (
            mock.patch("sunbeam.provider.local.steps.get_admin_connection"),
            mock.patch(
                "sunbeam.provider.local.steps.guests_on_hypervisor",
                return_value=[],
            ) as guests,
        ):
            yield guests

    def test_run_with_config(self):
        step, client, jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)
        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED
        jhelper.grant_secret.assert_called_once_with(
            "machine-model", "secret:abc", "vaultlocker-hypervisor"
        )
        client.cluster.get_node_info.assert_called_once_with(NODE)
        jhelper.get_unit_from_machine.assert_called_once_with(
            "openstack-hypervisor", "5", "machine-model"
        )
        jhelper.get_leader_unit.assert_not_called()
        jhelper.run_action.assert_called_once_with(
            "openstack-hypervisor/1",
            "machine-model",
            "configure-encrypted-storage",
            {
                "target": "/dev/disk/by-id/wwn-0x1",
                "existing-key-secret-id": "secret:abc",
            },
            timeout=1800,
        )
        jhelper.add_secret.assert_not_called()

    def test_run_fails_when_instances_remain(self, no_guests):
        no_guests.return_value = [mock.MagicMock(), mock.MagicMock()]
        step, _client, jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)

        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED
        assert "2 instance" in result.message
        jhelper.add_secret.assert_not_called()
        jhelper.grant_secret.assert_not_called()
        jhelper.run_action.assert_not_called()

    def test_run_interactive_creates_and_removes_secret(self):
        step, client, jhelper = make_step(None)
        step.target = "/dev/disk/by-id/wwn-0x2"
        step.passphrase = "s3cret"
        jhelper.add_secret.return_value = "secret:temp"
        jhelper.run_action.return_value = {
            "status": "completed",
            "target": "/dev/disk/by-id/wwn-0x2",
        }

        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED
        jhelper.add_secret.assert_called_once()
        jhelper.grant_secret.assert_called_once_with(
            "machine-model",
            "compute-storage-compute-1-example-com",
            "vaultlocker-hypervisor",
        )
        jhelper.remove_secret.assert_called_once_with(
            "machine-model", "compute-storage-compute-1-example-com"
        )
        assert step.passphrase is None

    def test_run_waits_for_mount_before_removing_temporary_secret(self):
        step, _client, jhelper = make_step()
        step.target = "/dev/disk/by-id/wwn-0x2"
        step.passphrase = "s3cret"
        jhelper.add_secret.return_value = "secret:temp"
        statuses = iter(("requested", "completed"))

        def action(*args, **kwargs):
            jhelper.remove_secret.assert_not_called()
            return {"status": next(statuses), "target": step.target}

        jhelper.run_action.side_effect = action
        with mock.patch("sunbeam.steps.hypervisor.time.sleep") as sleep:
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_count == 2
        sleep.assert_called_once()
        jhelper.remove_secret.assert_called_once()

    def test_run_timeout_removes_temporary_secret(self):
        step, _client, jhelper = make_step()
        step.target = "/dev/disk/by-id/wwn-0x2"
        step.passphrase = "s3cret"
        jhelper.add_secret.return_value = "secret:temp"
        jhelper.run_action.return_value = {"status": "requested", "target": step.target}
        with (
            mock.patch(
                "sunbeam.steps.hypervisor.time.monotonic",
                side_effect=[0, 0, 0, 1801],
            ),
            mock.patch("sunbeam.steps.hypervisor.time.sleep"),
        ):
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message
        jhelper.remove_secret.assert_called_once()

    def test_grant_failure_removes_temporary_secret(self):
        step, _client, jhelper = make_step()
        step.target = "/dev/disk/by-id/wwn-0x2"
        step.passphrase = "s3cret"
        jhelper.add_secret.return_value = "secret:temp"
        jhelper.grant_secret.side_effect = ActionFailedException("grant failed")

        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED
        jhelper.remove_secret.assert_called_once()
        jhelper.run_action.assert_not_called()

    def test_secret_removal_failure_is_reported(self):
        step, _client, jhelper = make_step()
        step.target = "/dev/disk/by-id/wwn-0x2"
        step.passphrase = "s3cret"
        jhelper.add_secret.return_value = "secret:temp"
        jhelper.run_action.return_value = {
            "status": "completed",
            "target": step.target,
        }
        jhelper.remove_secret.side_effect = JujuException("remove failed")

        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED
        assert "could not remove temporary secret" in result.message

    def test_run_action_failure(self):
        step, _client, jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)
        jhelper.run_action.side_effect = ActionFailedException("action failed")

        result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED

    def test_prompt_interactive(self):
        step, _client, _jhelper = make_step(None)
        with (
            mock.patch(
                "sunbeam.provider.local.steps.sunbeam.core.questions.PromptQuestion"
            ) as prompt_question,
            mock.patch(
                "sunbeam.provider.local.steps.sunbeam.core.questions."
                "PasswordPromptQuestion"
            ) as password_question,
        ):
            prompt_question.return_value.ask.return_value = "/dev/disk/by-id/wwn-0x9"
            password_question.return_value.ask.return_value = "s3cret"

            step.prompt(Console(), show_hint=False)

        assert step.target == "/dev/disk/by-id/wwn-0x9"
        assert step.passphrase == "s3cret"
        prompt_question.return_value.ask.assert_called_once()
        password_question.assert_called_once_with(
            "Existing LUKS passphrase", password=True
        )
