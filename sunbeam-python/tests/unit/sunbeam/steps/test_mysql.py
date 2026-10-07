# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, call, patch

import pytest
from jubilant.statustypes import AppStatus, FormattedBase, StatusInfo, UnitStatus

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType, SunbeamException
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    LeaderNotFoundException,
)
from sunbeam.steps.mysql import (
    MAX_RESUME_ATTEMPTS,
    MYSQL_ROUTER_CHARM,
    MYSQL_UPGRADE_CONFIG_KEY,
    ROUTER_REFRESH_TIMEOUT,
    MySQLCharmUpgradeStep,
    MySQLRouterCharmRefreshStep,
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
        self, step, method_name, target_state, step_context
    ):
        step.state = target_state
        getattr(step, method_name)(step_context)

        assert step.state == target_state

    def test_is_skip_application_not_deployed(self, step, basic_jhelper, step_context):
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException()

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_revision_pinned_in_manifest(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        step_context,
    ):
        basic_jhelper.get_application.return_value = Mock(charm_rev=255, base=None)
        basic_manifest.find_charm.return_value = Mock(
            revision=255, channel="8.0/stable"
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_branch_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """Branch channels proceed with refresh to pick up any new revision."""
        basic_jhelper.get_application.return_value = Mock(
            charm_rev=255,
            base=None,
            charm_channel="8.0/edge/my-fix-branch",
            charm_name="mysql-k8s",
        )
        basic_manifest.find_charm.return_value = Mock(revision=None, channel="8.0/edge")
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.get_available_charm_revisions.assert_not_called()

    def test_is_skip_channel_track_mismatch(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        basic_jhelper.get_application.return_value = Mock(
            charm_rev=255, base=None, charm_channel="8.0/stable"
        )
        basic_manifest.find_charm.return_value = Mock(
            revision=None, channel="9.0/stable"
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_already_latest(self, step, basic_jhelper, step_context):
        app = Mock(charm_rev=343, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 343}
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_already_latest_on_deployed_channel(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        """A manifest on a different channel does not change the latest check"""
        basic_manifest.find_charm.return_value = Mock(revision=None, channel="8.0/edge")
        basic_jhelper.get_application.return_value = Mock(
            charm_rev=200, base=None, charm_channel="8.0/candidate"
        )
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_out_of_band_upgrade(
        self, step, basic_jhelper, basic_client, step_context
    ):
        app = Mock(charm_rev=255, base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 343}
        basic_jhelper.show_unit.return_value = {
            "relation-info": [
                {
                    "endpoint": "upgrade",
                    "application-data": {"upgrade-stack": "[0, 1]"},
                }
            ]
        }
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_upgrade_needed(
        self, step, basic_jhelper, basic_manifest, step_context
    ):
        charm_manifest = Mock(revision=None, channel="8.0/stable")
        basic_manifest.find_charm.return_value = charm_manifest
        app = Mock(charm_rev=255, charm_channel="8.0/stable", base=None)
        basic_jhelper.get_application.return_value = app
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 343}
        basic_jhelper.show_unit.return_value = {}

        result = step.is_skip(step_context)

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

    def test_record_original_state_revision_and_scale(
        self, step, basic_jhelper, step_context
    ):
        app = Mock(charm_rev=255, scale=3)
        basic_jhelper.get_application.return_value = app

        step.record_original_state(step_context)

        assert step.original_revision == 255
        assert step.original_scale == 3
        assert step.state == MySQLUpgradeState.ORIGINAL_STATE_RECORDED

    def test_scale_up_happy_path(self, step, basic_jhelper, step_context):
        step.original_scale = 2
        basic_jhelper.wait_until_active.return_value = None

        step.scale_up(step_context)

        basic_jhelper.scale_application.assert_called_once_with(
            step.model, step.application, 3
        )
        basic_jhelper.wait_until_active.assert_called_once()
        assert step.state == MySQLUpgradeState.SCALED_UP

    def test_scale_up_juju_failure(self, step, basic_jhelper, step_context):
        step.original_scale = 2
        basic_jhelper.scale_application.return_value = None
        basic_jhelper.wait_until_active.side_effect = TimeoutError()

        with pytest.raises(SunbeamException) as exc:
            step.scale_up(step_context)
        assert "timed out" in str(exc).lower()

    def test_run_precheck_happy_path(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.return_value = "mysql/0"

        step.run_precheck(step_context)

        basic_jhelper.run_action.assert_called_once_with(
            "mysql/0", step.model, "pre-upgrade-check"
        )
        assert step.state == MySQLUpgradeState.PRECHECK_DONE

    def test_run_precheck_no_leader(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.side_effect = LeaderNotFoundException()

        with pytest.raises(SunbeamException) as exc:
            step.run_precheck(step_context)

        assert "unable to determine leader" in str(exc).lower()

    def test_refresh_and_wait_highest(self, step, basic_jhelper, step_context):
        app = Mock(units=["mysql/0", "mysql/1"])
        basic_jhelper.get_application.return_value = app
        step._wait_for_highest_upgrade = Mock()

        step.refresh_and_wait_highest(step_context)

        basic_jhelper.charm_refresh.assert_called_once()
        step._wait_for_highest_upgrade.assert_called_once_with("mysql/1")
        assert step.state == MySQLUpgradeState.HIGHEST_UNIT_UPGRADED

    def test_wait_for_highest_upgrade_timeout(self, step, basic_jhelper, step_context):
        app = Mock(units=["mysql/0", "mysql/1"])
        basic_jhelper.get_application.return_value = app

        step._wait_for_highest_upgrade = Mock(side_effect=TimeoutError())
        step.state = MySQLUpgradeState.SCALED_UP

        with pytest.raises(SunbeamException) as exc:
            step.refresh_and_wait_highest(step_context)
        assert "timed out" in str(exc).lower()

    def test_resume_upgrade(self, step, basic_jhelper, step_context):
        basic_jhelper.get_leader_unit.return_value = "mysql/0"

        step.resume_upgrade(step_context)

        basic_jhelper.run_action.assert_called_once_with(
            "mysql/0", step.model, "resume-upgrade", {}
        )
        assert step.state == MySQLUpgradeState.UPGRADE_RESUMED

    def test_wait_until_active_timeout_rollback_hint(
        self, step, basic_jhelper, step_context
    ):
        step.original_revision = 255
        basic_jhelper.wait_until_active.side_effect = TimeoutError()

        with pytest.raises(SunbeamException) as exc:
            step.wait_until_active(step_context)

        assert "rollback" in str(exc).lower()

    def test_scale_back_skipped_when_original_scale_unknown(self, step, step_context):
        step.original_scale = None

        step.scale_back(step_context)

        assert step.state != MySQLUpgradeState.SCALED_BACK

    def test_scale_back_success(self, step, basic_jhelper, step_context):
        step.original_scale = 2
        app = Mock(scale=3)
        basic_jhelper.get_application.return_value = app

        step.scale_back(step_context)

        basic_jhelper.scale_application.assert_called_once_with(
            step.model, step.application, 2
        )
        assert step.state == MySQLUpgradeState.SCALED_BACK


class TestReapplyMySQLTerraformPlanStep:
    def test_get_mysql_terraform_targets(
        self, basic_deployment, basic_client, basic_jhelper, basic_manifest
    ):
        """Test generation of MySQL-specific terraform targets."""
        from sunbeam.steps.mysql import ReapplyMySQLTerraformPlanStep

        tfhelper = Mock()
        step = ReapplyMySQLTerraformPlanStep(
            basic_deployment, basic_client, tfhelper, basic_jhelper, basic_manifest
        )

        # Mock get_application_names to return some mysql-router apps
        basic_jhelper.get_application_names.return_value = [
            "mysql-k8s",
            "keystone",
            "keystone-mysql-router",
            "nova-api",
            "nova-api-mysql-router",
            "glance",
            "glance-mysql-router",
            "manila-data-mysql-router",
        ]
        tfhelper.state_list.return_value = [
            "module.keystone[0].juju_application.mysql-router",
            "module.keystone[0].juju_integration.mysql-router-to-mysql",
            "module.keystone[0].juju_integration.service-to-mysql-router",
            "module.nova[0].juju_application.nova-api-mysql-router[0]",
            "module.nova[0].juju_integration.nova-api-to-mysql-router[0]",
            "module.nova[0].juju_integration.nova-api-mysql-router-to-metrics-endpoint[0]",
            "module.glance[0].juju_application.mysql-router",
            "module.glance[0].juju_integration.mysql-router-to-mysql",
        ]

        targets = step._get_mysql_terraform_targets()

        # Check that basic mysql targets are included
        assert "-target=module.single-mysql" in targets
        assert "-target=module.many-mysql" in targets

        # Check that only mysql-router resources that exist in state are targeted.
        assert "-target=module.keystone[0].juju_application.mysql-router" in targets
        assert (
            "-target=module.keystone[0].juju_integration.mysql-router-to-mysql"
            in targets
        )
        assert (
            "-target=module.nova[0].juju_application.nova-api-mysql-router[0]"
            in targets
        )
        assert (
            "-target=module.nova[0].juju_integration.nova-api-mysql-router-to-metrics-endpoint[0]"
            in targets
        )
        assert (
            "-target=module.manila-data[0].juju_application.mysql-router" not in targets
        )

    def test_run_success(
        self, basic_deployment, basic_client, basic_jhelper, basic_manifest
    ):
        """Test successful MySQL terraform apply with targets."""
        from sunbeam.steps.mysql import ReapplyMySQLTerraformPlanStep

        tfhelper = Mock()
        step = ReapplyMySQLTerraformPlanStep(
            basic_deployment, basic_client, tfhelper, basic_jhelper, basic_manifest
        )

        # Mock get_application_names
        basic_jhelper.get_application_names.return_value = [
            "mysql-k8s",
            "keystone-mysql-router",
        ]
        tfhelper.state_list.return_value = [
            "module.keystone[0].juju_application.mysql-router",
            "module.keystone[0].juju_integration.mysql-router-to-mysql",
        ]

        # Mock wait_until_active
        basic_jhelper.wait_until_active.return_value = None

        context = Mock()
        context.reporter = Mock()
        result = step.run(context)

        # Check that terraform was applied with targets
        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        call_args = tfhelper.update_tfvars_and_apply_tf.call_args
        assert call_args.kwargs["tf_apply_extra_args"] is not None
        assert (
            len(call_args.kwargs["tf_apply_extra_args"]) > 2
        )  # More than just base targets

        # Check that we waited for the right applications
        basic_jhelper.wait_until_active.assert_called_once()
        call_args = basic_jhelper.wait_until_active.call_args
        assert call_args.kwargs["apps"] == ["mysql", "keystone-mysql-router"]

        assert result.result_type == ResultType.COMPLETED

    def test_run_terraform_failure(
        self, basic_deployment, basic_client, basic_jhelper, basic_manifest
    ):
        """Test MySQL terraform apply failure."""
        from sunbeam.core.terraform import TerraformException
        from sunbeam.steps.mysql import ReapplyMySQLTerraformPlanStep

        tfhelper = Mock()
        step = ReapplyMySQLTerraformPlanStep(
            basic_deployment, basic_client, tfhelper, basic_jhelper, basic_manifest
        )

        # Mock terraform failure
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "Terraform apply failed"
        )

        context = Mock()
        context.reporter = Mock()
        result = step.run(context)

        assert result.result_type == ResultType.FAILED
        assert "Terraform apply failed" in result.message

    def test_run_wait_timeout(
        self, basic_deployment, basic_client, basic_jhelper, basic_manifest
    ):
        """Test MySQL terraform apply with wait timeout."""
        from sunbeam.core.juju import JujuWaitException
        from sunbeam.steps.mysql import ReapplyMySQLTerraformPlanStep

        tfhelper = Mock()
        step = ReapplyMySQLTerraformPlanStep(
            basic_deployment, basic_client, tfhelper, basic_jhelper, basic_manifest
        )

        # Mock successful terraform but timeout on wait
        basic_jhelper.get_application_names.return_value = ["mysql-k8s"]
        tfhelper.state_list.return_value = []
        basic_jhelper.wait_until_active.side_effect = JujuWaitException(
            "Timeout waiting"
        )

        context = Mock()
        context.reporter = Mock()
        result = step.run(context)

        assert result.result_type == ResultType.FAILED
        assert "Timeout waiting" in result.message


ROUTER_PAUSED_MESSAGE = (
    "Refreshing. Check units >=2 are healthy & run `resume-refresh` on leader."
)


def _router_app(
    rev=100,
    channel="8.0/stable",
    scale=1,
    app_status="active",
    message="",
    upgrading_from="",
    charm_name=MYSQL_ROUTER_CHARM,
    unit_status="active",
    agent_status="idle",
):
    return AppStatus(
        charm=f"ch:amd64/{charm_name}-{rev}",
        charm_origin="charmhub",
        charm_name=charm_name,
        charm_rev=rev,
        exposed=False,
        base=FormattedBase(name="ubuntu", channel="22.04"),
        charm_channel=channel,
        scale=scale,
        app_status=StatusInfo(current=app_status, message=message),
        units={
            f"u/{i}": UnitStatus(
                workload_status=StatusInfo(current=unit_status),
                juju_status=StatusInfo(current=agent_status),
                upgrading_from=upgrading_from,
            )
            for i in range(scale)
        },
    )


ROUTER_APP = "keystone-mysql-router"
OTHER_APP = "glance-mysql-router"
ROUTER_STATES = {
    "single_before": {},
    "single": {"rev": 200},
    "before": {"scale": 3},
    "done": {"scale": 3, "rev": 200},
    "rolling": {"scale": 3, "app_status": "maintenance"},
    "paused": {
        "scale": 3,
        "app_status": "blocked",
        "message": ROUTER_PAUSED_MESSAGE,
    },
    "paused_unhealthy": {
        "scale": 3,
        "app_status": "blocked",
        "message": ROUTER_PAUSED_MESSAGE,
        "unit_status": "maintenance",
        "agent_status": "executing",
    },
}


class TestMySQLRouterCharmRefreshStep:
    @pytest.fixture
    def step(self, basic_deployment, basic_client, basic_jhelper, basic_manifest):
        basic_manifest.find_charm.return_value = None
        basic_client.cluster.get_config.side_effect = ConfigItemNotFoundException()
        basic_jhelper.get_available_charm_revisions.return_value = {"amd64": 200}
        basic_jhelper.get_leader_unit.return_value = f"{ROUTER_APP}/0"
        return MySQLRouterCharmRefreshStep(
            basic_deployment, basic_client, basic_jhelper, basic_manifest
        )

    @pytest.fixture
    def refresh_step(self, step):
        """Step as left by is_skip, ready for run()"""
        step.apps_to_refresh = [ROUTER_APP]
        step.apps_to_wait = [ROUTER_APP]
        return step

    def _run_polling(self, refresh_step, step_context, *states):
        """Run with the app moving through states, the first is run()'s initial"""
        refresh_step.jhelper.get_model_status.side_effect = [
            Mock(apps={ROUTER_APP: _router_app(**ROUTER_STATES[s])}) for s in states
        ]
        with patch("sunbeam.steps.mysql.time.sleep"):
            return refresh_step.run(step_context)

    def _resume_calls(self, jhelper):
        """The resume-refresh run_action calls, excluding pre-refresh-check"""
        return [
            c
            for c in jhelper.run_action.call_args_list
            if c.args[2] == "resume-refresh"
        ]

    def _fail_resume(self, jhelper):
        """Make resume-refresh fail while pre-refresh-check succeeds"""

        def run_action(unit, model, action, *args, **kwargs):
            if action == "resume-refresh":
                raise ActionFailedException("terminated")
            return {}

        jhelper.run_action.side_effect = run_action

    def test_is_skip_when_mysql_upgrade_in_progress(
        self, step, basic_client, basic_jhelper, step_context
    ):
        basic_client.cluster.get_config.side_effect = None
        basic_client.cluster.get_config.return_value = json.dumps(
            {"state": "SCALED_UP"}
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED
        basic_jhelper.get_model_status.assert_not_called()

    def test_is_skip_outdated_router_selected(self, step, basic_jhelper, step_context):
        basic_jhelper.get_model_status.return_value = Mock(
            apps={
                "mysql": _router_app(rev=1, charm_name="mysql-k8s"),
                "keystone-mysql-router": _router_app(rev=100),
                "glance-mysql-router": _router_app(rev=200),
            }
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.apps_to_refresh == ["keystone-mysql-router"]
        assert step.apps_to_wait == ["keystone-mysql-router"]
        # charmhub lookup is cached per channel/base
        basic_jhelper.get_available_charm_revisions.assert_called_once()

    @pytest.mark.parametrize(
        "app_kwargs",
        [
            {"upgrading_from": "ch:amd64/mysql-router-k8s-100"},
            {"app_status": "blocked", "message": ROUTER_PAUSED_MESSAGE},
        ],
    )
    def test_is_skip_waits_for_router_left_mid_refresh(
        self, step, basic_jhelper, step_context, app_kwargs
    ):
        """Routers already at target revision are still waited on if mid refresh"""
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(rev=200, scale=3, **app_kwargs)}
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.apps_to_refresh == []
        assert step.apps_to_wait == [ROUTER_APP]

    @pytest.mark.parametrize(
        "manifest_charm, deployed_channel, rev, expected",
        [
            (None, "8.0/stable", 200, ResultType.SKIPPED),  # already at latest
            (None, "8.0/stable", 100, ResultType.COMPLETED),
            (
                Mock(channel="8.0/stable", revision=150),
                "8.0/stable",
                200,
                ResultType.COMPLETED,  # manifest pinned revision differs
            ),
            (
                Mock(channel="8.4/stable", revision=None),
                "8.0/stable",
                100,
                ResultType.SKIPPED,  # track change is not an in-channel refresh
            ),
            (
                Mock(channel="8.0/stable", revision=None),
                "8.0/candidate",
                100,
                ResultType.SKIPPED,  # lower risk than deployed
            ),
            (
                Mock(channel="8.0/edge/fix", revision=None),
                "8.0/edge",
                200,
                ResultType.COMPLETED,  # branch target
            ),
            (
                Mock(channel="8.0/edge", revision=None),
                "8.0/edge/fix",
                200,
                ResultType.COMPLETED,  # deployed branch
            ),
        ],
    )
    def test_is_skip_refresh_decision(
        self,
        step,
        basic_jhelper,
        basic_manifest,
        step_context,
        manifest_charm,
        deployed_channel,
        rev,
        expected,
    ):
        basic_manifest.find_charm.return_value = manifest_charm
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(rev=rev, channel=deployed_channel)}
        )

        result = step.is_skip(step_context)

        assert result.result_type == expected

    def test_is_skip_charmhub_lookup_failure_refreshes(
        self, step, basic_jhelper, step_context
    ):
        """A failed Charmhub lookup must not abort the command. Refresh anyway."""
        basic_jhelper.get_available_charm_revisions.side_effect = JujuException(
            "charmhub unreachable"
        )
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(rev=100)}
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert step.apps_to_refresh == [ROUTER_APP]

    def test_run_single_unit_no_resume(self, refresh_step, basic_jhelper, step_context):
        result = self._run_polling(
            refresh_step, step_context, "single_before", "single"
        )

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.set_app_config.assert_called_once_with(
            ROUTER_APP, refresh_step.model, {"pause-after-unit-refresh": "first"}
        )
        basic_jhelper.run_action.assert_called_once_with(
            f"{ROUTER_APP}/0", refresh_step.model, "pre-refresh-check"
        )
        basic_jhelper.charm_refresh.assert_called_once_with(
            ROUTER_APP, refresh_step.model, channel="8.0/stable", revision=None
        )

    def test_run_fails_when_pre_refresh_check_fails(
        self, refresh_step, basic_jhelper, step_context
    ):
        """Unrefreshed routers must not let the mysql-k8s upgrade proceed"""
        basic_jhelper.run_action.side_effect = ActionFailedException("not ready")

        result = self._run_polling(
            refresh_step, step_context, "single_before", "single_before"
        )

        assert result.result_type == ResultType.FAILED
        assert ROUTER_APP in result.message
        assert "not ready" in result.message
        basic_jhelper.charm_refresh.assert_not_called()

    def test_run_fails_when_no_leader_for_pre_refresh_check(
        self, refresh_step, basic_jhelper, step_context
    ):
        basic_jhelper.get_leader_unit.side_effect = LeaderNotFoundException()

        result = self._run_polling(
            refresh_step, step_context, "single_before", "single_before"
        )

        assert result.result_type == ResultType.FAILED
        basic_jhelper.charm_refresh.assert_not_called()

    def test_run_refreshes_other_apps_when_one_fails_pre_refresh_check(
        self, refresh_step, basic_jhelper, step_context
    ):
        refresh_step.apps_to_refresh = [OTHER_APP, ROUTER_APP]
        refresh_step.apps_to_wait = [OTHER_APP, ROUTER_APP]

        def get_leader_unit(name, model):
            if name == ROUTER_APP:
                raise LeaderNotFoundException()
            return f"{name}/0"

        basic_jhelper.get_leader_unit.side_effect = get_leader_unit
        before = _router_app(**ROUTER_STATES["before"])
        done = _router_app(**ROUTER_STATES["done"])
        basic_jhelper.get_model_status.side_effect = [
            Mock(apps={OTHER_APP: before, ROUTER_APP: before}),
            Mock(apps={OTHER_APP: done, ROUTER_APP: before}),
        ]

        with patch("sunbeam.steps.mysql.time.sleep"):
            result = refresh_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert ROUTER_APP in result.message
        assert OTHER_APP not in result.message
        basic_jhelper.charm_refresh.assert_called_once()
        assert basic_jhelper.charm_refresh.call_args.args[0] == OTHER_APP

    def test_run_resumes_paused_refresh(
        self, refresh_step, basic_jhelper, step_context
    ):
        result = self._run_polling(
            refresh_step, step_context, "before", "paused", "done"
        )

        assert result.result_type == ResultType.COMPLETED
        assert self._resume_calls(basic_jhelper) == [
            call(f"{ROUTER_APP}/0", refresh_step.model, "resume-refresh")
        ]

    def test_run_resumes_again_on_second_pause_round(
        self, refresh_step, basic_jhelper, step_context
    ):
        result = self._run_polling(
            refresh_step,
            step_context,
            "before",
            "paused",
            "rolling",
            "paused",
            "done",
        )

        assert result.result_type == ResultType.COMPLETED
        assert len(self._resume_calls(basic_jhelper)) == 2

    def test_run_does_not_resume_until_units_healthy(
        self, refresh_step, basic_jhelper, step_context
    ):
        result = self._run_polling(
            refresh_step, step_context, "before", "paused_unhealthy", "done"
        )

        assert result.result_type == ResultType.COMPLETED
        assert self._resume_calls(basic_jhelper) == []

    def test_run_settles_app_whose_units_still_report_upgrading_from(
        self, refresh_step, basic_jhelper, step_context
    ):
        # Waited on but not refreshed by this step
        refresh_step.apps_to_refresh = []
        basic_jhelper.get_model_status.return_value = Mock(
            apps={
                ROUTER_APP: _router_app(
                    rev=200, scale=3, upgrading_from="ch:amd64/mysql-router-k8s-100"
                )
            }
        )

        result = refresh_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_not_called()

    def test_run_tolerates_failed_resume_action(
        self, refresh_step, basic_jhelper, step_context
    ):
        """resume-refresh restarts the leader which can abort the action."""
        self._fail_resume(basic_jhelper)

        result = self._run_polling(
            refresh_step, step_context, "before", "paused", "done"
        )

        assert result.result_type == ResultType.COMPLETED

    def test_run_retries_failed_resume_then_fails(
        self, refresh_step, basic_jhelper, step_context
    ):
        """A resume that never unpauses the app is retried a bounded number of times"""
        self._fail_resume(basic_jhelper)

        result = self._run_polling(
            refresh_step, step_context, "before", *(["paused"] * 30)
        )

        assert result.result_type == ResultType.FAILED
        assert "resume-refresh did not unstick" in result.message
        assert len(self._resume_calls(basic_jhelper)) == MAX_RESUME_ATTEMPTS

    def test_run_waits_for_other_apps_after_one_is_stuck(
        self, refresh_step, basic_jhelper, step_context
    ):
        """A stuck app is reported only once every other app has also concluded"""
        refresh_step.apps_to_refresh = [OTHER_APP, ROUTER_APP]
        refresh_step.apps_to_wait = [OTHER_APP, ROUTER_APP]
        self._fail_resume(basic_jhelper)
        before = _router_app(**ROUTER_STATES["before"])
        paused = _router_app(**ROUTER_STATES["paused"])
        rolling = _router_app(**ROUTER_STATES["rolling"])
        done = _router_app(**ROUTER_STATES["done"])
        basic_jhelper.get_model_status.side_effect = [
            Mock(apps={OTHER_APP: before, ROUTER_APP: before}),
            *[Mock(apps={OTHER_APP: rolling, ROUTER_APP: paused})] * 25,
            Mock(apps={OTHER_APP: done, ROUTER_APP: paused}),
        ]

        with patch("sunbeam.steps.mysql.time.sleep"):
            result = refresh_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert ROUTER_APP in result.message
        assert OTHER_APP not in result.message
        # The final status, where the other app settles, was polled
        assert basic_jhelper.get_model_status.call_count == 27

    def test_run_tolerates_missing_leader_during_resume(
        self, refresh_step, basic_jhelper, step_context
    ):
        """Leader election after a restart must not crash the poll loop"""
        basic_jhelper.get_leader_unit.side_effect = [
            f"{ROUTER_APP}/0",  # pre-refresh-check
            LeaderNotFoundException(),  # first resume attempt
            f"{ROUTER_APP}/0",  # retry
        ]

        result = self._run_polling(
            refresh_step, step_context, "before", "paused", "paused", "done"
        )

        assert result.result_type == ResultType.COMPLETED
        assert self._resume_calls(basic_jhelper) == [
            call(f"{ROUTER_APP}/0", refresh_step.model, "resume-refresh")
        ]

    def test_run_does_not_settle_on_pre_refresh_healthy_state(
        self, refresh_step, basic_jhelper, step_context
    ):
        """Active/idle on the old revision is not a completed refresh"""
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(**ROUTER_STATES["before"])}
        )

        with (
            patch(
                "sunbeam.steps.mysql.time.monotonic",
                side_effect=[0.0, ROUTER_REFRESH_TIMEOUT + 1],
            ),
            patch("sunbeam.steps.mysql.time.sleep"),
        ):
            result = refresh_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "timed out" in result.message

    def test_run_settles_on_channel_change_at_same_revision(
        self, refresh_step, basic_jhelper, basic_manifest, step_context
    ):
        basic_manifest.find_charm.return_value = Mock(channel="8.0/edge", revision=None)
        basic_jhelper.get_model_status.side_effect = [
            Mock(apps={ROUTER_APP: _router_app(rev=200, channel="8.0/stable")}),
            Mock(apps={ROUTER_APP: _router_app(rev=200, channel="8.0/edge")}),
        ]

        result = refresh_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED

    @pytest.mark.parametrize(
        "channel, lookup_error",
        [
            ("8.0/edge/fix", None),  # branch channels aren't in the channel map
            ("8.0/stable", JujuException("charmhub unreachable")),
        ],
    )
    def test_run_settles_refresh_with_unverifiable_target_at_same_revision(
        self, refresh_step, basic_jhelper, step_context, channel, lookup_error
    ):
        """With no way to know a newer revision exists a no-op refresh must not hang."""
        basic_jhelper.get_available_charm_revisions.side_effect = lookup_error
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(rev=100, channel=channel)}
        )

        with patch("sunbeam.steps.mysql.time.sleep"):
            result = refresh_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_called_once()

    def test_run_treats_removed_app_as_settled(
        self, refresh_step, basic_jhelper, step_context
    ):
        """An app removed from the model must not block the wait loop."""
        basic_jhelper.get_model_status.return_value = Mock(apps={})

        result = refresh_step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.charm_refresh.assert_not_called()

    def test_run_timeout_fails(self, refresh_step, basic_jhelper, step_context):
        basic_jhelper.get_model_status.return_value = Mock(
            apps={ROUTER_APP: _router_app(**ROUTER_STATES["rolling"])}
        )

        with (
            patch(
                "sunbeam.steps.mysql.time.monotonic",
                side_effect=[0.0, ROUTER_REFRESH_TIMEOUT + 1],
            ),
            patch("sunbeam.steps.mysql.time.sleep"),
        ):
            result = refresh_step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert ROUTER_APP in result.message
