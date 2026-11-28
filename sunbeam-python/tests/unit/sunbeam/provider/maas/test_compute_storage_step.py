# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for MAAS encrypted compute-storage configuration."""

from types import SimpleNamespace
from unittest import mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.compute_storage import ComputeStorageConfig, NodeStorageConfig
from sunbeam.core.juju import ActionFailedException
from sunbeam.provider.maas.steps import MaasConfigureEncryptedStorageStep

NODE = "compute-1.example.com"
NODE_2 = "compute-2.example.com"


def make_config(two_hosts=False):
    nodes = {
        NODE: NodeStorageConfig(
            target="/dev/disk/by-id/wwn-0x1", existing_key_secret_id="secret:abc"
        )
    }
    if two_hosts:
        nodes[NODE_2] = NodeStorageConfig(
            target="/dev/disk/by-id/wwn-0x2", existing_key_secret_id="secret:def"
        )
    return ComputeStorageConfig(vault_offer_url="vault.vault-kv", nodes=nodes)


def make_step(config=None):
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
        deployment=mock.MagicMock(),
    )
    step.update_status = mock.MagicMock()
    return step, client, jhelper


@pytest.fixture(autouse=True)
def no_guests():
    """Use Nova hosts with no instances unless a test sets another result."""
    with (
        mock.patch("sunbeam.provider.maas.steps.get_admin_connection") as connection,
        mock.patch(
            "sunbeam.steps.hypervisor.guests_on_hypervisor", return_value=[]
        ) as guests,
    ):
        connection.return_value.compute.hypervisors.return_value = [
            SimpleNamespace(name=NODE),
            SimpleNamespace(name=NODE_2),
        ]
        guests.connection = connection.return_value
        guests.get_connection = connection
        yield guests


def test_enrolls_configured_host(no_guests):
    step, client, jhelper = make_step()

    assert step.run(mock.MagicMock()).result_type == ResultType.COMPLETED

    no_guests.get_connection.assert_called_once()
    no_guests.assert_called_once_with(NODE, no_guests.connection)
    client.cluster.get_node_info.assert_called_once_with(NODE)
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
        timeout=600,
    )


def test_admin_connection_failure_stops_before_enrollment(no_guests):
    no_guests.get_connection.side_effect = ActionFailedException("login failed")
    step, _client, jhelper = make_step(make_config(two_hosts=True))

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert "login failed" in result.message
    no_guests.get_connection.assert_called_once()
    jhelper.grant_secret.assert_not_called()


def test_action_failure_does_not_skip_other_host(no_guests):
    step, client, jhelper = make_step(make_config(two_hosts=True))
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
    assert f"{NODE}: boom" in result.message
    assert jhelper.run_action.call_count == 2
    assert jhelper.grant_secret.call_count == 2
    no_guests.get_connection.assert_called_once()


def test_host_with_guests_does_not_skip_other_host(no_guests):
    step, _client, jhelper = make_step(make_config(two_hosts=True))
    no_guests.side_effect = [[mock.MagicMock()], []]
    jhelper.run_action.return_value = {
        "status": "completed",
        "target": "/dev/disk/by-id/wwn-0x2",
    }

    result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.FAILED
    assert NODE in result.message
    assert "instance" in result.message
    jhelper.grant_secret.assert_called_once_with(
        "machine-model", "secret:def", "vaultlocker-hypervisor"
    )
    jhelper.run_action.assert_called_once()
    no_guests.get_connection.assert_called_once()


def test_waits_for_completion_on_each_host(no_guests):
    step, _client, jhelper = make_step(make_config(two_hosts=True))
    jhelper.run_action.side_effect = [
        {"status": "requested", "target": "/dev/disk/by-id/wwn-0x1"},
        {"status": "completed", "target": "/dev/disk/by-id/wwn-0x1"},
        {"status": "completed", "target": "/dev/disk/by-id/wwn-0x2"},
    ]

    with mock.patch("sunbeam.steps.hypervisor.time.sleep"):
        result = step.run(mock.MagicMock())

    assert result.result_type == ResultType.COMPLETED
    assert jhelper.run_action.call_count == 3
    no_guests.get_connection.assert_called_once()
