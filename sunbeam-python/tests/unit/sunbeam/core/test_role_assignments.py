# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import yaml

from sunbeam.core.role_assignments import (
    build_microovn_role_mapping,
    dump_role_mapping,
)


def test_microovn_role_mapping_without_split_roles_assigns_compute_gateways():
    client = _client_with_nodes(
        {
            "control": [{"machineid": "0", "role": ["control"]}],
            "compute": [
                {"machineid": "1", "role": ["compute"]},
                {"machineid": "2", "role": ["compute", "network"]},
            ],
            "network": [{"machineid": "2", "role": ["compute", "network"]}],
        }
    )

    mapping = build_microovn_role_mapping(
        client,
        model_name="openstack-machines",
        split_roles=False,
        machine_ids=["0", "1", "2"],
        assign_central_roles=True,
    )

    assert mapping == {
        "openstack-machines": {
            "microovn": {
                "machines": {
                    "0": {"roles": ["chassis", "central"]},
                    "1": {"roles": ["chassis", "gateway"]},
                    "2": {"roles": ["chassis", "gateway"]},
                }
            }
        }
    }


def test_microovn_role_mapping_with_split_roles_keeps_gateways_on_network_nodes():
    client = _client_with_nodes(
        {
            "control": [{"machineid": "0", "role": ["control"]}],
            "compute": [{"machineid": "1", "role": ["compute"]}],
            "network": [{"machineid": "2", "role": ["network"]}],
        }
    )

    mapping = build_microovn_role_mapping(
        client,
        model_name="openstack-machines",
        split_roles=True,
        machine_ids=["0", "1", "2"],
        assign_central_roles=True,
    )

    assert mapping["openstack-machines"]["microovn"]["machines"] == {
        "0": {"roles": ["chassis", "central"]},
        "1": {"roles": ["chassis"]},
        "2": {"roles": ["chassis", "gateway"]},
    }


def test_microovn_role_mapping_filters_to_actual_microovn_machines():
    client = _client_with_nodes(
        {
            "control": [{"machineid": "0", "role": ["control"]}],
            "compute": [{"machineid": "1", "role": ["compute"]}],
            "network": [{"machineid": "2", "role": ["network"]}],
        }
    )

    mapping = build_microovn_role_mapping(
        client,
        model_name="openstack-machines",
        split_roles=False,
        machine_ids=["2"],
        assign_central_roles=True,
    )

    assert mapping["openstack-machines"]["microovn"]["machines"] == {
        "2": {"roles": ["chassis", "gateway"]},
    }


def test_microovn_role_mapping_can_disable_central_roles():
    client = _client_with_nodes(
        {
            "control": [{"machineid": "0", "role": ["control", "network"]}],
            "compute": [],
            "network": [{"machineid": "0", "role": ["control", "network"]}],
        }
    )

    mapping = build_microovn_role_mapping(
        client,
        model_name="openstack-machines",
        split_roles=True,
        machine_ids=["0"],
        assign_central_roles=False,
    )

    assert mapping["openstack-machines"]["microovn"]["machines"] == {
        "0": {"roles": ["chassis", "gateway"]},
    }


def test_microovn_role_mapping_allows_central_and_gateway_for_provider_microovn():
    client = _client_with_nodes(
        {
            "control": [{"machineid": "0", "role": ["control", "network"]}],
            "compute": [],
            "network": [{"machineid": "0", "role": ["control", "network"]}],
        }
    )

    mapping = build_microovn_role_mapping(
        client,
        model_name="openstack-machines",
        split_roles=True,
        machine_ids=["0"],
        assign_central_roles=True,
    )

    assert mapping["openstack-machines"]["microovn"]["machines"] == {
        "0": {"roles": ["chassis", "central", "gateway"]},
    }


def test_dump_role_mapping_serializes_valid_yaml():
    mapping = {
        "openstack-machines": {
            "microovn": {
                "machines": {
                    "0": {"roles": ["chassis", "central"]},
                }
            }
        }
    }

    dumped = dump_role_mapping(mapping)

    assert yaml.safe_load(dumped) == mapping


def _client_with_nodes(nodes_by_role):
    class Cluster:
        def list_nodes_by_role(self, role):
            return nodes_by_role.get(role, [])

    class Client:
        cluster = Cluster()

    return Client()
