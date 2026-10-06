# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for capacity policy preflight check."""

import json
from unittest.mock import MagicMock

import pytest

from sunbeam.upgrades.preflight.capacity import (
    CapacityCheck,
    CapacityPolicy,
    load_capacity_policy,
)
from sunbeam.upgrades.preflight.checks import CheckContext

FROM = "2025.1"
TO = "2026.1"


def _make_unit(name: str) -> tuple[str, MagicMock]:
    return name, MagicMock()


def _make_app(units: dict[str, MagicMock]) -> MagicMock:
    app = MagicMock()
    app.units = units
    return app


@pytest.fixture
def mock_client():
    client = MagicMock()
    return client


@pytest.fixture
def mock_jhelper():
    return MagicMock()


@pytest.fixture
def mock_deployment(mock_client, mock_jhelper):
    deployment = MagicMock()
    deployment.openstack_machines_model = "openstack-machines"
    deployment.get_client.return_value = mock_client
    deployment.get_juju_helper.return_value = mock_jhelper
    return deployment


@pytest.fixture
def ctx(mock_deployment):
    return CheckContext(
        deployment=mock_deployment,
        from_release=FROM,
        to_release=TO,
    )


# ---------------------------------------------------------------------------
# CapacityPolicy
# ---------------------------------------------------------------------------


class TestCapacityPolicy:
    def test_defaults(self):
        policy = CapacityPolicy()
        assert policy.min_free_percentage == 25
        assert policy.min_free_nodes == 1

    def test_custom_values(self):
        policy = CapacityPolicy(min_free_percentage=50, min_free_nodes=3)
        assert policy.min_free_percentage == 50
        assert policy.min_free_nodes == 3


# ---------------------------------------------------------------------------
# load_capacity_policy
# ---------------------------------------------------------------------------


class TestLoadCapacityPolicy:
    def test_returns_defaults_when_config_absent(self, mock_client):
        mock_client.cluster.get_config.side_effect = Exception("not found")
        policy = load_capacity_policy(mock_client)
        assert policy.min_free_percentage == 25
        assert policy.min_free_nodes == 1

    def test_loads_from_clusterd_json_string(self, mock_client):
        mock_client.cluster.get_config.return_value = json.dumps(
            {"min_free_percentage": 50, "min_free_nodes": 2}
        )
        policy = load_capacity_policy(mock_client)
        assert policy.min_free_percentage == 50
        assert policy.min_free_nodes == 2

    def test_loads_from_clusterd_dict(self, mock_client):
        mock_client.cluster.get_config.return_value = {
            "min_free_percentage": 30,
            "min_free_nodes": 3,
        }
        policy = load_capacity_policy(mock_client)
        assert policy.min_free_percentage == 30
        assert policy.min_free_nodes == 3


# ---------------------------------------------------------------------------
# CapacityCheck
# ---------------------------------------------------------------------------


class TestCapacityCheck:
    def test_passes_when_enough_free_nodes(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(4))
        mock_jhelper.get_application.return_value = _make_app(units)
        # 1 busy, 3 free
        mock_jhelper.run_action.side_effect = [
            {"result": '["vm-1"]'},
            {"result": "[]"},
            {"result": "[]"},
            {"result": "[]"},
        ]
        check = CapacityCheck(ctx)
        assert check.run() is True

    def test_fails_when_no_free_nodes(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(2))
        mock_jhelper.get_application.return_value = _make_app(units)
        mock_jhelper.run_action.side_effect = [
            {"result": '["vm-1"]'},
            {"result": '["vm-2"]'},
        ]
        check = CapacityCheck(ctx)
        assert check.run() is False
        assert check.exit_code == 1
        assert "0 free" in check.message
        assert "at least 1" in check.message

    def test_fails_when_free_percentage_below_policy(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.return_value = json.dumps(
            {"min_free_percentage": 50, "min_free_nodes": 1}
        )
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(4))
        mock_jhelper.get_application.return_value = _make_app(units)
        # 3 busy, 1 free = 25% < 50%
        mock_jhelper.run_action.side_effect = [
            {"result": '["vm-1"]'},
            {"result": '["vm-2"]'},
            {"result": '["vm-3"]'},
            {"result": "[]"},
        ]
        check = CapacityCheck(ctx)
        assert check.run() is False
        assert "25%" in check.message
        assert "50%" in check.message

    def test_override_skips_check(self, ctx, mock_jhelper):
        check = CapacityCheck(ctx, override=True)
        assert check.run() is True
        mock_jhelper.get_application.assert_not_called()

    def test_fails_when_no_hypervisor_units(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        mock_jhelper.get_application.return_value = _make_app({})
        check = CapacityCheck(ctx)
        assert check.run() is False
        assert "No openstack-hypervisor units" in check.message

    def test_fails_when_app_not_found(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        mock_jhelper.get_application.side_effect = Exception("not deployed")
        check = CapacityCheck(ctx)
        assert check.run() is False
        assert "Cannot find" in check.message

    def test_fails_when_action_raises(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(2))
        mock_jhelper.get_application.return_value = _make_app(units)
        mock_jhelper.run_action.side_effect = Exception("action failed")
        check = CapacityCheck(ctx)
        assert check.run() is False
        assert "action failed" in check.message

    def test_passes_with_all_nodes_free(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(3))
        mock_jhelper.get_application.return_value = _make_app(units)
        mock_jhelper.run_action.side_effect = [
            {"result": "[]"},
            {"result": "[]"},
            {"result": "[]"},
        ]
        check = CapacityCheck(ctx)
        assert check.run() is True

    def test_handles_non_json_result_gracefully(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(2))
        mock_jhelper.get_application.return_value = _make_app(units)
        mock_jhelper.run_action.side_effect = [
            {"result": "not-json"},
            {"result": "[]"},
        ]
        check = CapacityCheck(ctx)
        # Invalid JSON is treated as empty (free), so 2 free out of 2
        assert check.run() is True

    def test_handles_missing_result_key(self, ctx, mock_jhelper):
        ctx.client.cluster.get_config.side_effect = Exception("not found")
        units = dict(_make_unit(f"openstack-hypervisor/{i}") for i in range(2))
        mock_jhelper.get_application.return_value = _make_app(units)
        mock_jhelper.run_action.side_effect = [
            {},
            {},
        ]
        check = CapacityCheck(ctx)
        assert check.run() is True
