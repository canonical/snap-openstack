# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    LeaderNotFoundException,
)
from sunbeam.steps.mysql import (
    MYSQL_UPGRADE_CONFIG_KEY,
    MySQLCharmUpgradeStep,
    MySQLUpgradeState,
    load_upgrade_state,
    write_upgrade_state,
)


@pytest.fixture
def step(basic_deployment, basic_client, basic_jhelper, basic_manifest):
    return MySQLCharmUpgradeStep(
        basic_deployment, basic_client, basic_jhelper, basic_manifest
    )


def test_mysql_upgrade_state_ordering():
    assert MySQLUpgradeState.SCALED_BACK >= MySQLUpgradeState.INIT
    assert not (MySQLUpgradeState.SCALED_UP >= MySQLUpgradeState.PRECHECK_DONE)
    assert MySQLUpgradeState.PRECHECK_DONE >= MySQLUpgradeState.PRECHECK_DONE


def test_mysql_upgrade_state_iteration_snapshot():
    assert list(MySQLUpgradeState) == [
        MySQLUpgradeState.INIT,
        MySQLUpgradeState.ORIGINAL_STATE_RECORDED,
        MySQLUpgradeState.SCALED_UP,
        MySQLUpgradeState.PRECHECK_DONE,
        MySQLUpgradeState.HIGHEST_UNIT_UPGRADED,
        MySQLUpgradeState.UPGRADE_RESUMED,
        MySQLUpgradeState.UNITS_SETTLED,
        MySQLUpgradeState.SCALED_BACK,
    ]


class TestMySqlUpgradeStatePersistence:
    def test_load_upgrade_state_valid_json(self, basic_client):
        basic_client.cluster.get_config.return_value = json.dumps(
            {"state": "SCALED_UP", "original_scale": 3, "original_revision": 255}
        )

        state = load_upgrade_state(basic_client)

        assert state["state"] == "SCALED_UP"
        assert state["original_scale"] == 3
        assert state["original_revision"] == 255

    def test_load_upgrade_state_missing_key(self, basic_client):
        basic_client.cluster.get_config.side_effect = ConfigItemNotFoundException()

        state = load_upgrade_state(basic_client)
        assert state == {}

    def test_write_upgrade_state(self, basic_client):
        state = {"state": "INIT", "original_revision": 255, "original_scale": 1}

        write_upgrade_state(basic_client, state)
        basic_client.cluster.update_config.assert_called_once_with(
            MYSQL_UPGRADE_CONFIG_KEY,
            json.dumps(state),
        )


class TestMySQLCharmUpgradeStep:
    @pytest.mark.parametrize(
        "method_name, target_state",
        [
            ("record_original_state", MySQLUpgradeState.ORIGINAL_STATE_RECORDED),
            ("scale_up", MySQLUpgradeState.SCALED_UP),
            ("run_precheck", MySQLUpgradeState.PRECHECK_DONE),
            ("refresh_and_wait_highest", MySQLUpgradeState.HIGHEST_UNIT_UPGRADED),
            ("resume_upgrade", MySQLUpgradeState.UPGRADE_RESUMED),
            ("wait_until_active", MySQLUpgradeState.UNITS_SETTLED),
            ("scale_back", MySQLUpgradeState.SCALED_BACK),
        ],
    )
    def test_step_is_noop_if_state_already_passed(
        self, step, method_name, target_state
    ):
        step.state = target_state
        getattr(step, method_name)()

        assert step.state == target_state

    def test_is_skip_application_not_deployed(self, step, basic_jhelper):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_revision_pinned_in_manifest(
        self, step, basic_jhelper, basic_manifest
    ):
        basic_jhelper.get_application.return_value = Mock(charm_rev=255, base=None)
        basic_manifest.core.software.charms.get.return_value = Mock(
            revision=255, channel="8.0/stable"
        )

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_channel_track_mismatch(self, step, basic_jhelper, basic_manifest):
        basic_jhelper.get_application.return_value = Mock(
            charm_rev=255, base=None, charm_channel="8.0/stable"
        )
        basic_manifest.core.software.charms.get.return_value = Mock(
            revision=None, channel="9.0/stable"
        )

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_already_latest(self, step, basic_jhelper):
        app = Mock(charm_rev=343, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 343
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_out_of_band_upgrade(self, step, basic_jhelper, basic_client):
        app = Mock(charm_rev=255, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 343
        basic_jhelper.show_unit.return_value = {
            "relation-info": [
                {
                    "endpoint": "upgrade",
                    "application-data": {"upgrade-stack": "[0, 1]"},
                }
            ]
        }
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_upgrade_needed(self, step, basic_jhelper, basic_manifest):
        charm_manifest = Mock(revision=None, channel="8.0/stable")
        basic_manifest.core.software.charms.get.return_value = charm_manifest
        app = Mock(charm_rev=255, charm_channel="8.0/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revision.return_value = 343
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    @pytest.mark.parametrize(
        "original_scale, expected_target",
        [
            (1, 3),
            (2, 3),
            (3, 5),
        ],
    )
    def test_target_scale_for_upgrade(self, step, original_scale, expected_target):
        assert step._target_scale_for_upgrade(original_scale) == expected_target

    def test_record_original_state_revision_and_scale(self, step, basic_jhelper):
        app = Mock(charm_rev=255, scale=3)
        basic_jhelper.get_application.return_value = app

        step.record_original_state()

        assert step.original_revision == 255
        assert step.original_scale == 3
        assert step.state == MySQLUpgradeState.ORIGINAL_STATE_RECORDED

    def test_scale_up_happy_path(self, step, basic_jhelper):
        step.original_scale = 2
        basic_jhelper.wait_until_active.return_value = None

        step.scale_up()

        basic_jhelper.scale_application.assert_called_once_with(
            step.model, step.application, 3
        )
        basic_jhelper.wait_until_active.assert_called_once()
        assert step.state == MySQLUpgradeState.SCALED_UP

    def test_scale_up_juju_failure(self, step, basic_jhelper):
        step.original_scale = 2
        basic_jhelper.scale_application.return_value = None
        basic_jhelper.wait_until_active.side_effect = TimeoutError()

        with pytest.raises(SunbeamException) as exc:
            step.scale_up()
        assert "timed out" in str(exc).lower()

    def test_run_precheck_happy_path(self, step, basic_jhelper):
        basic_jhelper.get_leader_unit.return_value = "mysql/0"

        step.run_precheck()

        basic_jhelper.run_action.assert_called_once_with(
            "mysql/0", step.model, "pre-upgrade-check"
        )
        assert step.state == MySQLUpgradeState.PRECHECK_DONE

    def test_run_precheck_no_leader(self, step, basic_jhelper):
        basic_jhelper.get_leader_unit.side_effect = LeaderNotFoundException()

        with pytest.raises(SunbeamException) as exc:
            step.run_precheck()

        assert "unable to determine leader" in str(exc).lower()

    def test_refresh_and_wait_highest(self, step, basic_jhelper):
        app = Mock(units=["mysql/0", "mysql/1"])
        basic_jhelper.get_application.return_value = app
        step._wait_for_highest_upgrade = Mock()

        step.refresh_and_wait_highest()

        basic_jhelper.charm_refresh.assert_called_once()
        step._wait_for_highest_upgrade.assert_called_once_with("mysql/1")
        assert step.state == MySQLUpgradeState.HIGHEST_UNIT_UPGRADED

    def test_wait_for_highest_upgrade_timeout(self, step, basic_jhelper):
        app = Mock(units=["mysql/0", "mysql/1"])
        basic_jhelper.get_application.return_value = app

        step._wait_for_highest_upgrade = Mock(side_effect=TimeoutError())
        step.state = MySQLUpgradeState.SCALED_UP

        with pytest.raises(SunbeamException) as exc:
            step.refresh_and_wait_highest()
        assert "timed out" in str(exc).lower()

    def test_resume_upgrade(self, step, basic_jhelper):
        basic_jhelper.get_leader_unit.return_value = "mysql/0"

        step.resume_upgrade()

        basic_jhelper.run_action.assert_called_once_with(
            "mysql/0", step.model, "resume-upgrade", {}
        )
        assert step.state == MySQLUpgradeState.UPGRADE_RESUMED

    def test_wait_until_active_timeout_rollback_hint(self, step, basic_jhelper):
        step.original_revision = 255
        basic_jhelper.wait_until_active.side_effect = TimeoutError()

        with pytest.raises(SunbeamException) as exc:
            step.wait_until_active()

        assert "rollback" in str(exc).lower()

    def test_scale_back_skipped_when_original_scale_unknown(self, step):
        step.original_scale = None

        step.scale_back()

        assert step.state != MySQLUpgradeState.SCALED_BACK

    def test_scale_back_success(self, step, basic_jhelper):
        step.original_scale = 2
        app = Mock(scale=3)
        basic_jhelper.get_application.return_value = app

        step.scale_back()

        basic_jhelper.scale_application.assert_called_once_with(
            step.model, step.application, 2
        )
        assert step.state == MySQLUpgradeState.SCALED_BACK
