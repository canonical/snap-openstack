# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for local encrypted compute-storage configuration."""

from types import SimpleNamespace
from unittest import mock

import jubilant
import pytest
from rich.console import Console

from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.compute_storage import NodeStorageConfig
from sunbeam.core.juju import ActionFailedException, JujuException
from sunbeam.provider.local.steps import LocalConfigureEncryptedStorageStep

NODE = "compute-1.example.com"
TARGET = "/dev/disk/by-id/wwn-0x1"
SECRET_NAME = "compute-storage-compute-1-example-com"


def make_step(node_config=None):
    client = mock.MagicMock()
    client.cluster.get_node_info.return_value = {"machineid": "5"}
    jhelper = mock.MagicMock()
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/1"
    jhelper.run_action.return_value = {"status": "completed", "target": TARGET}
    step = LocalConfigureEncryptedStorageStep(
        client,
        NODE,
        jhelper,
        "machine-model",
        mock.MagicMock(),
        node_config=node_config,
    )
    step.update_status = mock.MagicMock()
    return step, jhelper


@pytest.fixture(autouse=True)
def no_guests():
    """Use a Nova host with no instances unless a test sets another result."""
    with (
        mock.patch("sunbeam.provider.local.steps.get_admin_connection") as connection,
        mock.patch(
            "sunbeam.steps.hypervisor.guests_on_hypervisor", return_value=[]
        ) as guests,
    ):
        connection.return_value.compute.hypervisors.return_value = [
            SimpleNamespace(name=NODE)
        ]
        guests.connection = connection.return_value
        yield guests


def test_configured_host_enrolls_without_prompt():
    step, jhelper = make_step(
        NodeStorageConfig(target=TARGET, existing_key_secret_id="secret:abc")
    )

    assert step.has_prompts() is False
    assert step.run(mock.MagicMock()).result_type == ResultType.COMPLETED
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", "secret:abc", "vaultlocker-hypervisor"
    )
    jhelper.run_action.assert_called_once_with(
        "openstack-hypervisor/1",
        "machine-model",
        "configure-encrypted-storage",
        {"target": TARGET, "existing-key-secret-id": "secret:abc"},
        timeout=600,
    )
    jhelper.add_secret.assert_not_called()


def test_interactive_prompt_collects_target_and_masked_passphrase():
    step, _jhelper = make_step()
    with (
        mock.patch(
            "sunbeam.provider.local.steps.sunbeam.core.questions.PromptQuestion"
        ) as target_question,
        mock.patch(
            "sunbeam.provider.local.steps.sunbeam.core.questions.PasswordPromptQuestion"
        ) as password_question,
    ):
        target_question.return_value.ask.return_value = TARGET
        password_question.return_value.ask.return_value = "s3cret"
        assert step.has_prompts() is True
        step.prompt(Console())

    assert step.target == TARGET
    assert step.passphrase == "s3cret"
    password_question.assert_called_once_with("Existing LUKS passphrase", password=True)


@pytest.mark.parametrize(
    ("target", "passphrase", "message"),
    [
        ("   ", "s3cret", "target is required"),
        (TARGET, "   ", "passphrase is required"),
    ],
)
def test_interactive_prompt_rejects_blank_answers(target, passphrase, message):
    step, _jhelper = make_step()
    with (
        mock.patch(
            "sunbeam.provider.local.steps.sunbeam.core.questions.PromptQuestion"
        ) as target_question,
        mock.patch(
            "sunbeam.provider.local.steps.sunbeam.core.questions.PasswordPromptQuestion"
        ) as password_question,
    ):
        target_question.return_value.ask.return_value = target
        password_question.return_value.ask.return_value = passphrase
        with pytest.raises(SunbeamException, match=message):
            step.prompt(Console())


def test_guests_prevent_temporary_secret_creation(no_guests):
    no_guests.return_value = [mock.MagicMock()]
    step, jhelper = make_step()
    step.target = TARGET
    step.passphrase = "s3cret"

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "1 instance" in result.message
    jhelper.add_secret.assert_not_called()
    jhelper.grant_secret.assert_not_called()


def test_interactive_secret_is_removed_after_enrollment():
    step, jhelper = make_step()
    step.target = TARGET
    step.passphrase = "s3cret"
    jhelper.add_secret.return_value = "temp"
    statuses = iter(("requested", "completed"))

    def action(*args, **kwargs):
        jhelper.remove_secret.assert_not_called()
        assert args[3]["existing-key-secret-id"] == "secret:temp"
        return {"status": next(statuses), "target": TARGET}

    jhelper.run_action.side_effect = action
    with mock.patch("sunbeam.steps.hypervisor.time.sleep"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    assert jhelper.run_action.call_count == 2
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", SECRET_NAME, "vaultlocker-hypervisor"
    )
    jhelper.remove_secret.assert_called_once_with("machine-model", SECRET_NAME)
    assert step.passphrase is None


def test_timeout_removes_temporary_secret():
    step, jhelper = make_step()
    step.target = TARGET
    step.passphrase = "s3cret"
    jhelper.add_secret.return_value = "temp"
    jhelper.run_action.return_value = {"status": "requested", "target": TARGET}
    with (
        mock.patch(
            "sunbeam.steps.hypervisor.time.monotonic", side_effect=[0, 0, 0, 601]
        ),
        mock.patch("sunbeam.steps.hypervisor.time.sleep"),
    ):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "Check the Vaultlocker subordinate unit" in result.message
    jhelper.remove_secret.assert_called_once_with("machine-model", SECRET_NAME)


def test_grant_failure_removes_temporary_secret():
    step, jhelper = make_step()
    step.target = TARGET
    step.passphrase = "s3cret"
    jhelper.add_secret.return_value = "temp"
    jhelper.grant_secret.side_effect = ActionFailedException("grant failed")

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "grant failed" in result.message
    jhelper.remove_secret.assert_called_once_with("machine-model", SECRET_NAME)
    jhelper.run_action.assert_not_called()


@pytest.mark.parametrize(
    ("task_message", "expected_reason"),
    [
        ("disk contains existing data", "disk contains existing data"),
        ("", "status 'failed', return code 0"),
    ],
)
def test_failed_action_reports_unit_and_reason(task_message, expected_reason):
    step, jhelper = make_step(
        NodeStorageConfig(target=TARGET, existing_key_secret_id="secret:abc")
    )
    task = jubilant.Task(
        id="294",
        status="failed",
        return_code=0,
        message=task_message,
    )
    jhelper.run_action.side_effect = jubilant.TaskError(task)

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "configure-encrypted-storage" in result.message
    assert "openstack-hypervisor/1" in result.message
    assert expected_reason in result.message


@pytest.mark.parametrize("action_fails", [False, True])
def test_cleanup_failure_reports_enrollment_outcome(action_fails):
    step, jhelper = make_step()
    step.target = TARGET
    step.passphrase = "s3cret"
    jhelper.add_secret.return_value = "temp"
    if action_fails:
        jhelper.run_action.side_effect = ActionFailedException("")
    jhelper.remove_secret.side_effect = JujuException("remove failed")

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert (
        "Enrollment failed" if action_fails else "Enrollment completed"
    ) in result.message
    assert "could not remove temporary secret" in result.message
