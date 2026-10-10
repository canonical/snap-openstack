# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local encrypted compute-storage step."""

from unittest import mock

import pytest
from rich.console import Console

from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.core.juju import ActionFailedException
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


def make_step(config=None, deployment=None, result_tfvars=None):
    client = mock.MagicMock()
    jhelper = mock.MagicMock()
    step = LocalConfigureEncryptedStorageStep(
        client,
        NODE,
        jhelper,
        "machine-model",
        deployment or mock.MagicMock(),
        config=config,
        result_tfvars=result_tfvars,
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
        assert step.vault_offer_url == "vault.vault-kv"

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
        jhelper.get_leader_unit.return_value = "openstack-hypervisor/0"

        with mock.patch(
            "sunbeam.provider.local.steps.set_vault_kv_offer_url"
        ) as set_offer:
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED
        set_offer.assert_called_once_with(client, "vault.vault-kv")
        jhelper.grant_secret.assert_called_once_with(
            "machine-model", "secret:abc", "vaultlocker-hypervisor"
        )
        jhelper.run_action.assert_called_once_with(
            "openstack-hypervisor/0",
            "machine-model",
            "configure-encrypted-storage",
            {
                "target": "/dev/disk/by-id/wwn-0x1",
                "existing-key-secret-id": "secret:abc",
            },
            timeout=1800,
        )
        jhelper.add_secret.assert_not_called()

    def test_run_populates_result_tfvars(self):
        result_tfvars: dict = {}
        step, client, jhelper = make_step(make_config(), result_tfvars=result_tfvars)
        step.prompt(Console(), show_hint=False)
        jhelper.get_leader_unit.return_value = "openstack-hypervisor/0"

        with (
            mock.patch("sunbeam.provider.local.steps.set_vault_kv_offer_url"),
            mock.patch(
                "sunbeam.provider.local.steps.get_vault_kv_offer_url",
                return_value="vault.vault-kv",
            ),
        ):
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.COMPLETED
        assert result_tfvars == {"vault-kv-offer-url": "vault.vault-kv"}

    def test_run_fails_when_instances_remain(self, no_guests):
        no_guests.return_value = [mock.MagicMock(), mock.MagicMock()]
        step, _client, jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)

        with mock.patch("sunbeam.provider.local.steps.set_vault_kv_offer_url"):
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
        jhelper.get_leader_unit.return_value = "openstack-hypervisor/0"

        with mock.patch.object(step, "vault_offer_url", None):
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

    def test_run_action_failure(self):
        step, _client, jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)
        jhelper.run_action.side_effect = ActionFailedException("action failed")

        with mock.patch("sunbeam.provider.local.steps.set_vault_kv_offer_url"):
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED

    def test_run_rejects_conflicting_offer(self):
        step, client, _jhelper = make_step(make_config())
        step.prompt(Console(), show_hint=False)

        with mock.patch(
            "sunbeam.provider.local.steps.set_vault_kv_offer_url",
            side_effect=ValueError("different vault-kv offer"),
        ):
            result = step.run(mock.MagicMock())

        assert result.result_type == ResultType.FAILED
        _ = client

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
            mock.patch(
                "sunbeam.provider.local.steps.get_vault_kv_offer_url",
                return_value=None,
            ),
        ):
            prompt_question.return_value.ask.side_effect = [
                "/dev/disk/by-id/wwn-0x9",
                "vault.vault-kv",
            ]
            password_question.return_value.ask.return_value = "s3cret"

            step.prompt(Console(), show_hint=False)

        assert step.target == "/dev/disk/by-id/wwn-0x9"
        assert step.passphrase == "s3cret"
        assert step.vault_offer_url == "vault.vault-kv"

    def test_prompt_interactive_reuses_existing_offer(self):
        step, _client, _jhelper = make_step(None)
        with (
            mock.patch(
                "sunbeam.provider.local.steps.sunbeam.core.questions.PromptQuestion"
            ) as prompt_question,
            mock.patch(
                "sunbeam.provider.local.steps.sunbeam.core.questions."
                "PasswordPromptQuestion"
            ) as password_question,
            mock.patch(
                "sunbeam.provider.local.steps.get_vault_kv_offer_url",
                return_value="existing.vault-kv",
            ),
        ):
            prompt_question.return_value.ask.return_value = "/dev/disk/by-id/wwn-0x9"
            password_question.return_value.ask.return_value = "s3cret"

            step.prompt(Console(), show_hint=False)

        assert step.vault_offer_url is None
