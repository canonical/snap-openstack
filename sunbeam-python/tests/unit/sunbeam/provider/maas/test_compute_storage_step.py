# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MAAS encrypted compute-storage step."""

from unittest import mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.core.juju import ActionFailedException
from sunbeam.provider.maas.steps import MaasConfigureEncryptedStorageStep

NODE = "compute-1.example.com"
NODE_2 = "compute-2.example.com"


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


def make_multi_node_config():
    return ComputeStorageConfig(
        vault_offer_url="vault.vault-kv",
        nodes={
            NODE: NodeStorageConfig(
                target="/dev/disk/by-id/wwn-0x1",
                existing_key_secret_id="secret:abc",
            ),
            NODE_2: NodeStorageConfig(
                target="/dev/disk/by-id/wwn-0x2",
                existing_key_secret_id="secret:def",
            ),
        },
    )


def make_step(config=None, deployment=None, result_tfvars=None):
    client = mock.MagicMock()
    client.cluster.get_node_info.return_value = {"machine_id": "5"}
    jhelper = mock.MagicMock()
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/0"
    step = MaasConfigureEncryptedStorageStep(
        client,
        jhelper,
        "machine-model",
        config or make_config(),
        deployment=deployment or mock.MagicMock(),
        result_tfvars=result_tfvars,
    )
    step.update_status = mock.MagicMock()
    return step, client, jhelper


@pytest.fixture(autouse=True)
def no_guests():
    """Default the "no instances remain" guard to pass for every test."""
    with (
        mock.patch("sunbeam.provider.maas.steps.get_admin_connection"),
        mock.patch(
            "sunbeam.provider.maas.steps.guests_on_hypervisor",
            return_value=[],
        ) as guests,
    ):
        yield guests


def test_run_completes():
    step, client, jhelper = make_step()

    with mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url") as set_offer:
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    set_offer.assert_called_once_with(client, "vault.vault-kv")
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", "secret:abc", "vaultlocker-hypervisor"
    )
    client.cluster.get_node_info.assert_called_once_with(NODE)
    jhelper.get_unit_from_machine.assert_called_once_with(
        "openstack-hypervisor", "5", "machine-model"
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


def test_run_populates_result_tfvars():
    result_tfvars: dict = {}
    step, _client, _jhelper = make_step(result_tfvars=result_tfvars)

    with (
        mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url"),
        mock.patch(
            "sunbeam.provider.maas.steps.get_vault_kv_offer_url",
            return_value="vault.vault-kv",
        ),
    ):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    assert result_tfvars == {"vault-kv-offer-url": "vault.vault-kv"}


def test_run_fails_when_instances_remain(no_guests):
    no_guests.return_value = [mock.MagicMock()]
    step, _client, jhelper = make_step()

    with mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "1 instance" in result.message
    jhelper.grant_secret.assert_not_called()
    jhelper.run_action.assert_not_called()


def test_run_action_failure_is_reported_with_host():
    step, _client, jhelper = make_step()
    jhelper.run_action.side_effect = ActionFailedException("boom")

    with mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message


def test_run_rejects_conflicting_offer():
    step, _client, _jhelper = make_step()

    with mock.patch(
        "sunbeam.provider.maas.steps.set_vault_kv_offer_url",
        side_effect=ValueError("different vault-kv offer"),
    ):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED


def test_run_continues_after_one_node_action_failure():
    """One node's action failure must not prevent other nodes from being tried."""
    step, client, jhelper = make_step(make_multi_node_config())
    client.cluster.get_node_info.side_effect = [
        {"machine_id": "5"},
        {"machine_id": "6"},
    ]
    jhelper.get_unit_from_machine.side_effect = [
        "openstack-hypervisor/0",
        "openstack-hypervisor/1",
    ]
    jhelper.run_action.side_effect = [
        ActionFailedException("boom"),
        None,
    ]

    with mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert jhelper.run_action.call_count == 2
    assert jhelper.grant_secret.call_count == 2


def test_run_continues_after_one_node_has_instances(no_guests):
    """A node with remaining instances must not block sibling nodes."""
    step, client, jhelper = make_step(make_multi_node_config())
    client.cluster.get_node_info.return_value = {"machine_id": "6"}
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/1"
    no_guests.side_effect = [[mock.MagicMock()], []]

    with mock.patch("sunbeam.provider.maas.steps.set_vault_kv_offer_url"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert "instance" in result.message
    # Only the second (guest-free) node reaches grant/run_action.
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", "secret:def", "vaultlocker-hypervisor"
    )
    jhelper.run_action.assert_called_once()
