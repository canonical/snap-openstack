# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterable

import yaml

from sunbeam.clusterd.client import Client

MICROOVN_APPLICATION = "microovn"


RoleMapping = dict[str, dict[str, dict[str, dict[str, dict[str, list[str]]]]]]


def build_microovn_role_mapping(
    client: Client,
    model_name: str,
    machine_ids: Iterable[str],
    *,
    assign_central_roles: bool,
) -> RoleMapping:
    """Build the role-distributor mapping for MicroOVN."""
    machine_roles: dict[str, list[str]] = {}
    microovn_machine_ids = {str(machine_id) for machine_id in machine_ids}

    for role in ("control", "compute", "network"):
        for node in client.cluster.list_nodes_by_role(role):
            machine_id = node.get("machineid")
            if machine_id in (-1, None):
                continue
            machine_id_str = str(machine_id)
            if machine_id_str not in microovn_machine_ids:
                continue
            node_roles = set(node.get("role", []))
            machine_roles[machine_id_str] = _microovn_roles_for_node(
                node_roles,
                assign_central_roles,
            )

    return _build_mapping(
        model_name,
        MICROOVN_APPLICATION,
        machine_roles,
    )


def dump_role_mapping(mapping: RoleMapping) -> str:
    """Serialize role-distributor mapping to stable YAML."""
    return yaml.safe_dump(mapping, sort_keys=True)


def _build_mapping(
    model_name: str,
    application_name: str,
    machine_roles: dict[str, list[str]],
) -> RoleMapping:
    return {
        model_name: {
            application_name: {
                "machines": {
                    machine_id: {"roles": roles}
                    for machine_id, roles in sorted(machine_roles.items())
                }
            }
        }
    }


def _microovn_roles_for_node(
    node_roles: Iterable[str],
    assign_central_roles: bool,
) -> list[str]:
    role_set = set(node_roles)
    roles = ["chassis"]

    if assign_central_roles and "control" in role_set:
        roles.append("central")
    if "network" in role_set:
        roles.append("gateway")

    return roles
