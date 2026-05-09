# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core import ceph as ceph_module
from sunbeam.provider.maas import commands as maas_commands


def test_is_internal_ceph_enabled_feature_aware_uses_feature_state(mocker):
    deployment = Mock()
    deployment.get_feature_manager.return_value.is_feature_enabled.return_value = False
    mocker.patch.object(ceph_module, "is_internal_ceph_enabled", return_value=True)

    result = ceph_module.is_internal_ceph_enabled_feature_aware(deployment, Mock())

    assert result is True


def test_call_enabled_feature_join_hooks_passes_node_context():
    deployment = Mock()
    feature_manager = Mock()
    deployment.get_feature_manager.return_value = feature_manager

    client = Mock()
    client.cluster.get_node_info.side_effect = lambda name: {
        "name": name,
        "role": [f"role-{name}"],
    }

    maas_commands._call_enabled_feature_join_hooks(
        deployment, client, ["node-2", "node-1", "node-1"]
    )

    assert feature_manager.call_enabled_features_on_join.call_count == 2
    _, first_kwargs = feature_manager.call_enabled_features_on_join.call_args_list[0]
    _, second_kwargs = feature_manager.call_enabled_features_on_join.call_args_list[1]
    assert first_kwargs["node_name"] == "node-1"
    assert first_kwargs["roles"] == ["role-node-1"]
    assert first_kwargs["status"] == "joined"
    assert second_kwargs["node_name"] == "node-2"
    assert second_kwargs["roles"] == ["role-node-2"]
    assert second_kwargs["status"] == "joined"


def test_call_enabled_feature_depart_hooks_passes_node_context():
    deployment = Mock()
    node_info = {"name": "node-1", "role": ["storage"]}

    maas_commands._call_enabled_feature_depart_hooks(
        deployment, node_info, "node-1", force=True
    )

    deployment.get_feature_manager.return_value.call_enabled_features_on_depart.assert_called_once_with(
        deployment,
        node_info,
        node_name="node-1",
        roles=["storage"],
        status="departed",
        force=True,
    )


def test_ensure_default_ceph_feature_calls_feature():
    deployment = Mock()
    ceph_feature = Mock()
    deployment.get_feature_manager.return_value.resolve_feature.return_value = (
        ceph_feature
    )

    ceph_module.ensure_default_ceph_feature(
        deployment,
        show_hints=False,
        maas_client=Mock(),
        storage=["node-1"],
    )

    ceph_feature.enable_default_storage.assert_called_once_with(
        deployment,
        False,
        maas_client=ceph_feature.enable_default_storage.call_args.kwargs["maas_client"],
        storage=["node-1"],
    )
