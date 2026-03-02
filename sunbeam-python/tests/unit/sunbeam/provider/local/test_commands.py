# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.features.interface.v1.base import EnableDisableFeature
from sunbeam.provider.local import commands as local_commands


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (True, {"enabled": "true"}),
        (False, {"enabled": "false"}),
    ],
)
def test_set_ceph_feature_enabled_state_updates_feature_info(
    enabled: bool, expected: dict
):
    deployment = Mock()
    client = Mock()
    ceph_feature = Mock(spec=EnableDisableFeature)
    deployment.get_feature_manager.return_value.resolve_feature.return_value = (
        ceph_feature
    )

    local_commands._set_ceph_feature_enabled_state(deployment, client, enabled=enabled)

    ceph_feature.update_feature_info.assert_called_once_with(client, expected)


def test_is_microceph_necessary_feature_aware_uses_feature_state(mocker):
    deployment = Mock()
    deployment.get_feature_manager.return_value.is_feature_enabled.return_value = False
    mocker.patch.object(local_commands, "is_microceph_necessary", return_value=True)

    result = local_commands._is_microceph_necessary_feature_aware(deployment, Mock())

    assert result is True


def test_call_enabled_feature_join_hooks_passes_node_context():
    deployment = Mock()
    node_info = {"name": "node-1", "role": ["compute"]}

    local_commands._call_enabled_feature_join_hooks(
        deployment, node_info, "node-1", ["compute"]
    )

    deployment.get_feature_manager.return_value.call_enabled_features_on_join.assert_called_once_with(
        deployment,
        node_info,
        node_name="node-1",
        roles=["compute"],
        status="joined",
    )


def test_call_enabled_feature_depart_hooks_passes_node_context():
    deployment = Mock()
    node_info = {"name": "node-1", "role": ["storage"]}

    local_commands._call_enabled_feature_depart_hooks(
        deployment, node_info, "node-1", ["storage"], force=True
    )

    deployment.get_feature_manager.return_value.call_enabled_features_on_depart.assert_called_once_with(
        deployment,
        node_info,
        node_name="node-1",
        roles=["storage"],
        status="departed",
        force=True,
    )
