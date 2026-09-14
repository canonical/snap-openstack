# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Tests for control-plane pre/post-upgrade action orchestration."""

from unittest.mock import MagicMock, patch

import pytest

from sunbeam.upgrades.control_plane.actions import (
    run_actions,
    run_post_actions,
    run_pre_actions,
)
from sunbeam.upgrades.errors import UpgradeErrorCode
from sunbeam.upgrades.metadata import ActionScope, ActionSpec


def _make_app(charm="keystone-k8s"):
    app = MagicMock()
    app.charm = charm
    return app


def _make_status(apps=None):
    if apps is None:
        apps = {"keystone": _make_app("keystone-k8s")}
    status = MagicMock()
    status.apps = apps
    return status


def _make_action(action="pre-upgrade", apps=None, scope=ActionScope.LEADER):
    if apps is None:
        apps = ["keystone-k8s"]
    return ActionSpec(action=action, apps=apps, scope=scope)


@pytest.fixture
def mock_jhelper():
    jhelper = MagicMock()
    jhelper.get_model_status.return_value = _make_status()
    jhelper.get_leader_unit.return_value = "keystone/0"
    jhelper.run_action.return_value = {"result": "ok"}
    return jhelper


class TestRunActions:
    def test_leader_action_succeeds(self, mock_jhelper):
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        result = run_actions(mock_jhelper, actions)
        assert result.success is True
        mock_jhelper.run_action.assert_called_once_with(
            "keystone/0", "openstack", "pre-upgrade"
        )

    def test_all_units_action_succeeds(self, mock_jhelper):
        app_mock = MagicMock()
        app_mock.charm = "keystone-k8s"
        app_mock.units = {"keystone/0": MagicMock(), "keystone/1": MagicMock()}
        mock_jhelper.get_model_status.return_value = _make_status(
            {"keystone": app_mock}
        )
        mock_jhelper.get_application.return_value = app_mock
        actions = [_make_action("pre-upgrade", ["keystone-k8s"], ActionScope.ALL_UNITS)]
        result = run_actions(mock_jhelper, actions)
        assert result.success is True
        assert mock_jhelper.run_action.call_count == 2

    def test_action_failure_returns_error(self, mock_jhelper):
        mock_jhelper.run_action.side_effect = Exception("action failed")
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        result = run_actions(mock_jhelper, actions)
        assert result.success is False
        assert result.error_code == UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED

    def test_no_leader_returns_error(self, mock_jhelper):
        mock_jhelper.get_leader_unit.return_value = ""
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        result = run_actions(mock_jhelper, actions)
        assert result.success is False
        assert result.error_code == UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED

    def test_leader_not_found_returns_error(self, mock_jhelper):
        mock_jhelper.get_leader_unit.side_effect = Exception("not found")
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        result = run_actions(mock_jhelper, actions)
        assert result.success is False
        assert result.error_code == UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED

    def test_multiple_apps_in_one_action(self, mock_jhelper):
        mock_jhelper.get_leader_unit.side_effect = ["keystone/0", "nova/0"]
        mock_jhelper.get_model_status.return_value = _make_status(
            {
                "keystone": _make_app("keystone-k8s"),
                "nova": _make_app("nova-k8s"),
            }
        )
        actions = [_make_action("pre-upgrade", ["keystone-k8s", "nova-k8s"])]
        result = run_actions(mock_jhelper, actions)
        assert result.success is True
        assert mock_jhelper.run_action.call_count == 2

    def test_multiple_actions(self, mock_jhelper):
        mock_jhelper.get_leader_unit.side_effect = ["keystone/0", "keystone/0"]
        actions = [
            _make_action("pre-upgrade", ["keystone-k8s"]),
            _make_action("custom-action", ["keystone-k8s"]),
        ]
        result = run_actions(mock_jhelper, actions)
        assert result.success is True
        assert mock_jhelper.run_action.call_count == 2

    def test_empty_actions_list_succeeds(self, mock_jhelper):
        result = run_actions(mock_jhelper, [])
        assert result.success is True
        mock_jhelper.run_action.assert_not_called()

    def test_first_action_failure_stops(self, mock_jhelper):
        mock_jhelper.run_action.side_effect = Exception("failed")
        actions = [
            _make_action("pre-upgrade", ["keystone-k8s"]),
            _make_action("custom-action", ["keystone-k8s"]),
        ]
        result = run_actions(mock_jhelper, actions)
        assert result.success is False
        # Only first action attempted
        assert mock_jhelper.run_action.call_count == 1


class TestRunPreActions:
    def test_runs_actions_and_waits(self, mock_jhelper):
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        with patch("sunbeam.upgrades.control_plane.actions.time.sleep") as mock_sleep:
            result = run_pre_actions(mock_jhelper, actions)
        assert result.success is True
        mock_sleep.assert_called_once()

    def test_empty_actions_skips_wait(self, mock_jhelper):
        with patch("sunbeam.upgrades.control_plane.actions.time.sleep") as mock_sleep:
            result = run_pre_actions(mock_jhelper, [])
        assert result.success is True
        mock_sleep.assert_not_called()

    def test_action_failure_skips_wait(self, mock_jhelper):
        mock_jhelper.run_action.side_effect = Exception("failed")
        actions = [_make_action("pre-upgrade", ["keystone-k8s"])]
        with patch("sunbeam.upgrades.control_plane.actions.time.sleep") as mock_sleep:
            result = run_pre_actions(mock_jhelper, actions)
        assert result.success is False
        mock_sleep.assert_not_called()


class TestRunPostActions:
    def test_runs_actions(self, mock_jhelper):
        actions = [_make_action("post-upgrade", ["keystone-k8s"])]
        result = run_post_actions(mock_jhelper, actions)
        assert result.success is True

    def test_empty_actions_succeeds(self, mock_jhelper):
        result = run_post_actions(mock_jhelper, [])
        assert result.success is True

    def test_failure_returns_result_but_logs_warning(self, mock_jhelper):
        mock_jhelper.run_action.side_effect = Exception("failed")
        actions = [_make_action("post-upgrade", ["keystone-k8s"])]
        result = run_post_actions(mock_jhelper, actions)
        assert result.success is False
        assert result.error_code == UpgradeErrorCode.CONTROL_PLANE_ACTION_FAILED
