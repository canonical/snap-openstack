# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MAAS encrypted compute-storage step."""

from types import SimpleNamespace
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


def make_step(config=None, deployment=None):
    client = mock.MagicMock()
    client.cluster.get_node_info.return_value = {"machineid": "5"}
    jhelper = mock.MagicMock()
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/0"
    jhelper.run_action.return_value = {
        "status": "completed",
        "target": "/dev/disk/by-id/wwn-0x1",
    }
    step = MaasConfigureEncryptedStorageStep(
        client,
        jhelper,
        "machine-model",
        config or make_config(),
        deployment=deployment or mock.MagicMock(),
    )
    step.update_status = mock.MagicMock()
    return step, client, jhelper


@pytest.fixture(autouse=True)
def no_guests():
    """Default the "no instances remain" guard to pass for every test."""
    with (
        mock.patch("sunbeam.provider.maas.steps.get_admin_connection") as connection,
        mock.patch(
            "sunbeam.provider.maas.steps.guests_on_hypervisor",
            return_value=[],
        ) as guests,
    ):
        connection.return_value.compute.hypervisors.return_value = [
            SimpleNamespace(name=NODE),
            SimpleNamespace(name=NODE_2),
        ]
        guests.connection = connection.return_value
        yield guests


def test_run_completes():
    step, client, jhelper = make_step()

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
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


def test_run_fails_when_instances_remain(no_guests):
    no_guests.return_value = [mock.MagicMock()]
    step, _client, jhelper = make_step()

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "1 instance" in result.message
    jhelper.grant_secret.assert_not_called()
    jhelper.run_action.assert_not_called()


def test_run_uses_nova_fqdn_for_short_maas_name(no_guests):
    config = ComputeStorageConfig(
        vault_offer_url="vault.vault-kv",
        nodes={"cloud-1": make_config().nodes[NODE]},
    )
    step, _client, jhelper = make_step(config)
    no_guests.connection.compute.hypervisors.return_value = [
        SimpleNamespace(name="cloud-1.maas")
    ]
    no_guests.return_value = [mock.MagicMock()]

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    no_guests.assert_called_once_with("cloud-1.maas", no_guests.connection)
    jhelper.grant_secret.assert_not_called()
    jhelper.run_action.assert_not_called()


def test_run_refuses_when_nova_hypervisor_name_is_unknown(no_guests):
    no_guests.connection.compute.hypervisors.return_value = []
    step, _client, jhelper = make_step()

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "Cannot identify a unique Nova hypervisor" in result.message
    no_guests.assert_not_called()
    jhelper.grant_secret.assert_not_called()
    jhelper.run_action.assert_not_called()


def test_run_action_failure_is_reported_with_host():
    step, _client, jhelper = make_step()
    jhelper.run_action.side_effect = ActionFailedException("boom")

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message


def test_run_continues_after_one_node_action_failure():
    """One node's action failure must not prevent other nodes from being tried."""
    step, client, jhelper = make_step(make_multi_node_config())
    client.cluster.get_node_info.side_effect = [
        {"machineid": "5"},
        {"machineid": "6"},
    ]
    jhelper.get_unit_from_machine.side_effect = [
        "openstack-hypervisor/0",
        "openstack-hypervisor/1",
    ]
    jhelper.run_action.side_effect = [
        ActionFailedException("boom"),
        {"status": "completed", "target": "/dev/disk/by-id/wwn-0x2"},
    ]

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert jhelper.run_action.call_count == 2
    assert jhelper.grant_secret.call_count == 2


def test_run_continues_after_one_node_has_instances(no_guests):
    """A node with remaining instances must not block sibling nodes."""
    step, client, jhelper = make_step(make_multi_node_config())
    client.cluster.get_node_info.return_value = {"machineid": "6"}
    jhelper.get_unit_from_machine.return_value = "openstack-hypervisor/1"
    no_guests.side_effect = [[mock.MagicMock()], []]

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert "instance" in result.message
    # Only the second (guest-free) node reaches grant/run_action.
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", "secret:def", "vaultlocker-hypervisor"
    )
    jhelper.run_action.assert_called_once()


def test_run_waits_for_completion_on_each_host():
    step, client, jhelper = make_step(make_multi_node_config())
    client.cluster.get_node_info.side_effect = [
        {"machineid": "5"},
        {"machineid": "6"},
    ]
    jhelper.get_unit_from_machine.side_effect = [
        "openstack-hypervisor/0",
        "openstack-hypervisor/1",
    ]
    jhelper.run_action.side_effect = [
        {"status": "requested", "target": "/dev/disk/by-id/wwn-0x1"},
        {"status": "completed", "target": "/dev/disk/by-id/wwn-0x1"},
        {"status": "completed", "target": "/dev/disk/by-id/wwn-0x2"},
    ]

    with mock.patch("sunbeam.steps.hypervisor.time.sleep"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    assert jhelper.run_action.call_count == 3


def test_run_reports_unexpected_action_result():
    step, _client, jhelper = make_step()
    jhelper.run_action.return_value = {
        "status": "requested",
        "target": "/dev/disk/by-id/other",
    }

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert "Unexpected" in result.message
