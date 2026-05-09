# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core import ceph as ceph_module
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

    ceph_module.set_ceph_feature_enabled_state(deployment, client, enabled=enabled)

    ceph_feature.update_feature_info.assert_called_once_with(client, expected)


def test_is_internal_ceph_enabled_feature_aware_uses_feature_state(mocker):
    deployment = Mock()
    deployment.get_feature_manager.return_value.is_feature_enabled.return_value = False
    mocker.patch.object(ceph_module, "is_internal_ceph_enabled", return_value=True)

    result = ceph_module.is_internal_ceph_enabled_feature_aware(deployment, Mock())

    assert result is True


def test_is_internal_ceph_enabled_feature_aware_returns_false_while_disabling(
    mocker,
):
    """Disabling marker must short-circuit feature-aware check to False.

    The ceph_disabling marker must short-circuit to False so callers
    don't observe the transient window where mode=NONE has been written
    but feature_enabled is still True.
    """
    deployment = Mock()
    feature = Mock(spec=EnableDisableFeature)
    feature.get_feature_info.return_value = {"ceph_disabling": "true"}
    deployment.get_feature_manager.return_value.resolve_feature.return_value = feature
    deployment.get_feature_manager.return_value.is_feature_enabled.return_value = True
    mocker.patch.object(ceph_module, "is_internal_ceph_enabled", return_value=True)

    result = ceph_module.is_internal_ceph_enabled_feature_aware(deployment, Mock())

    assert result is False


def test_call_enabled_feature_join_hooks_passes_node_context():
    deployment = Mock()
    node_info = {"name": "node-1", "role": ["compute"]}

    local_commands._call_enabled_feature_join_hooks(
        deployment, node_info, "node-1", ["compute"], accept_defaults=True
    )

    deployment.get_feature_manager.return_value.call_enabled_features_on_join.assert_called_once_with(
        deployment,
        node_info,
        node_name="node-1",
        roles=["compute"],
        status="joined",
        accept_defaults=True,
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


def test_get_default_ceph_bootstrap_steps_delegates_to_feature():
    """The core helper resolves the ceph feature and forwards the flags."""
    deployment = Mock()
    feature = Mock()
    feature.get_bootstrap_deploy_steps = Mock(return_value=["STEP"])
    deployment.get_feature_manager.return_value.resolve_feature.return_value = feature

    result = ceph_module.get_default_ceph_bootstrap_steps(
        deployment,
        enabled=True,
        expect_storage_node=True,
        node_name="node-1",
        accept_defaults=True,
    )

    assert result == ["STEP"]
    feature.get_bootstrap_deploy_steps.assert_called_once_with(
        deployment,
        enabled=True,
        expect_storage_node=True,
        node_name="node-1",
        accept_defaults=True,
    )


def test_get_default_ceph_bootstrap_steps_returns_empty_when_feature_missing():
    """A missing or incompatible ceph feature must not crash callers."""
    deployment = Mock()
    deployment.get_feature_manager.return_value.resolve_feature.return_value = None

    result = ceph_module.get_default_ceph_bootstrap_steps(
        deployment,
        enabled=True,
        expect_storage_node=True,
    )

    assert result == []


def test_ensure_default_ceph_feature_calls_feature():
    deployment = Mock()
    ceph_feature = Mock()
    deployment.get_feature_manager.return_value.resolve_feature.return_value = (
        ceph_feature
    )

    ceph_module.ensure_default_ceph_feature(
        deployment,
        show_hints=False,
        node_name="node-1",
        accept_defaults=True,
    )

    ceph_feature.enable_default_storage.assert_called_once_with(
        deployment,
        False,
        node_name="node-1",
        accept_defaults=True,
    )
