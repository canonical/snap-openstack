# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    LeaderNotFoundException,
)
from sunbeam.steps.backup_restore import (
    MYSQL_CHARM,
    VAULT_CHARM,
    ActionTarget,
    MySQLBackupComponent,
    RestoreStep,
    _ActionStep,
    _component_for,
    _PauseAppStep,
    _RestoreAppStep,
    _ResumeAppStep,
    _ScaleAppStep,
)


def _mysql_component():
    return _component_for(MYSQL_CHARM)


def _vault_component():
    return _component_for(VAULT_CHARM)


class TestGuardedSteps:
    def test_action_step_default_runs_on_leader(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-k8s/0"

        step = _ActionStep(
            jhelper,
            name="Action",
            description="Run action",
            app="keystone-k8s",
            action_name="pause",
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.get_application.assert_not_called()
        jhelper.run_action.assert_called_once_with(
            "keystone-k8s/0",
            "openstack",
            "pause",
            timeout=120,
        )

    def test_pause_dispatches_action_on_all_units(self, step_context):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock(
            units={"keystone-k8s/0": Mock(), "keystone-k8s/1": Mock()}
        )

        step = _PauseAppStep(jhelper, app="keystone-k8s")
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.get_application_actions.assert_not_called()
        jhelper.get_leader_unit.assert_not_called()
        assert jhelper.run_action.call_count == 2
        assert jhelper.run_action.call_args_list[0].args == (
            "keystone-k8s/0",
            "openstack",
            "pause",
        )
        assert jhelper.run_action.call_args_list[1].args == (
            "keystone-k8s/1",
            "openstack",
            "pause",
        )

    def test_resume_dispatches_action_on_all_units(self, step_context):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock(
            units={"keystone-k8s/0": Mock(), "keystone-k8s/1": Mock()}
        )

        step = _ResumeAppStep(jhelper, app="keystone-k8s")
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.get_leader_unit.assert_not_called()
        assert jhelper.run_action.call_count == 2
        assert jhelper.run_action.call_args_list[0].args == (
            "keystone-k8s/0",
            "openstack",
            "resume",
        )
        assert jhelper.run_action.call_args_list[1].args == (
            "keystone-k8s/1",
            "openstack",
            "resume",
        )

    def test_restore_mysql_uses_latest_backup_id(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.run_action.side_effect = [
            {
                "backups": (
                    "backup-id | backup-type | backup-status\n"
                    "---------------------------------------\n"
                    "2026-07-15T00:00:00Z | physical | finished"
                )
            },
            {},
        ]
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "restore"
        )
        result = _RestoreAppStep(jhelper, _mysql_component(), target).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_args_list[1].args[2] == "restore"
        assert jhelper.run_action.call_args_list[1].args[3] == {
            "backup-id": "2026-07-15T00:00:00Z"
        }

    def test_restore_mysql_uses_restore_to_time(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        component = _mysql_component()
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "restore"
        )

        result = _RestoreAppStep(
            jhelper,
            component,
            target,
            restore_to_time="2026-07-15 00:00:00",
        ).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "keystone-mysql/0",
            "openstack",
            "restore",
            {"restore-to-time": "2026-07-15 00:00:00"},
            timeout=1800,
        )

    def test_restore_to_time_falls_back_when_component_does_not_support_it(
        self, step_context
    ):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.run_action.side_effect = [
            {"backup-ids": '["vault-backup-openstack-2026-07-15-00-03-28"]'},
            {},
        ]
        target = ActionTarget("vault", "vault/0", VAULT_CHARM, "restore-backup")

        result = _RestoreAppStep(
            jhelper,
            _vault_component(),
            target,
            restore_to_time="2026-07-15 00:00:00",
        ).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_args_list[1].args[2] == "restore-backup"
        assert jhelper.run_action.call_args_list[1].args[3] == {
            "backup-id": "vault-backup-openstack-2026-07-15-00-03-28"
        }

    def test_restore_vault_uses_latest_backup(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.run_action.side_effect = [
            {"backup-ids": '["vault-backup-openstack-2026-07-15-00-03-28"]'},
            {},
        ]
        target = ActionTarget("vault", "vault/0", VAULT_CHARM, "restore-backup")

        result = _RestoreAppStep(jhelper, _vault_component(), target).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_args_list[1].args[2] == "restore-backup"
        assert jhelper.run_action.call_args_list[1].args[3] == {
            "backup-id": "vault-backup-openstack-2026-07-15-00-03-28"
        }

    def test_mysql_restore_does_not_retry_action_failure(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        jhelper.run_action.side_effect = [
            {
                "backups": (
                    "backup-id | backup-type | backup-status\n"
                    "---------------------------------------\n"
                    "2026-07-15T00:00:00Z | physical | finished"
                )
            },
            ActionFailedException("transient restore failure"),
            {},
        ]
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "restore"
        )

        result = _RestoreAppStep(jhelper, _mysql_component(), target).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_action.call_count == 2

    def test_vault_restore_does_not_retry_action_failure(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "vault/0"
        jhelper.run_action.side_effect = [
            {"backup-ids": '["vault-backup-openstack-2026-07-15-00-03-28"]'},
            ActionFailedException("restore failure"),
        ]
        target = ActionTarget("vault", "vault/0", VAULT_CHARM, "restore-backup")

        result = _RestoreAppStep(jhelper, _vault_component(), target).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert jhelper.run_action.call_count == 2


class TestScaleMySQLStep:
    def test_scales_and_waits(self, step_context):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock(units={"keystone-mysql/0": Mock()})
        result = _ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.get_application.assert_called_once_with("keystone-mysql", "openstack")
        jhelper.scale_application.assert_called_once_with(
            "openstack", "keystone-mysql", 1
        )
        jhelper.wait_until_active.assert_called_once()

    def test_returns_failed_on_juju_error(self, step_context):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock(units={"keystone-mysql/0": Mock()})
        jhelper.scale_application.side_effect = JujuException("boom")
        result = _ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_scale_to_zero_waits_for_existing_units_to_leave(self, step_context):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock(
            units={"keystone-mysql-router/0": Mock(), "keystone-mysql-router/1": Mock()}
        )

        result = _ScaleAppStep(jhelper, "keystone-mysql-router", 0).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.wait_units_gone.assert_called_once_with(
            ["keystone-mysql-router/0", "keystone-mysql-router/1"],
            "openstack",
            timeout=120,
        )
        jhelper.wait_until_active.assert_not_called()


class TestMySQLControlPlaneResolution:
    def test_resolves_apps_for_shared_mysql_topology(self):
        jhelper = Mock()

        mysql_status = Mock(
            relations={
                "database": [
                    Mock(
                        interface="mysql_client",
                        related_app="keystone-mysql-router",
                    ),
                    Mock(interface="mysql_client", related_app="nova-mysql-router"),
                ]
            }
        )
        keystone_router_status = Mock(
            relations={
                "shared-db": [
                    Mock(interface="mysql_client", related_app="mysql"),
                    Mock(interface="mysql_client", related_app="keystone"),
                ]
            }
        )
        nova_router_status = Mock(
            relations={
                "shared-db": [
                    Mock(interface="mysql_client", related_app="mysql"),
                    Mock(interface="mysql_client", related_app="nova"),
                ]
            }
        )

        jhelper.get_model_status.return_value = Mock(
            apps={
                "mysql": mysql_status,
                "keystone": Mock(relations={}),
                "keystone-mysql-router": keystone_router_status,
                "nova": Mock(relations={}),
                "nova-mysql-router": nova_router_status,
            }
        )

        apps, routers = MySQLBackupComponent()._restore_apps(
            jhelper, "mysql", "openstack"
        )

        assert apps == ["keystone", "nova"]
        assert routers == ["keystone-mysql-router", "nova-mysql-router"]

    def test_resolves_all_per_service_routers_from_mysql_relations(self):
        jhelper = Mock()

        def router_status():
            return Mock(
                relations={
                    "database": [
                        Mock(interface="mysql_client", related_app="nova-mysql"),
                        Mock(interface="mysql_client", related_app="nova"),
                    ]
                }
            )

        jhelper.get_model_status.return_value = Mock(
            apps={
                "nova-mysql": Mock(
                    relations={
                        "database": [
                            Mock(
                                interface="mysql_client",
                                related_app="nova-mysql-router",
                            ),
                            Mock(
                                interface="mysql_client",
                                related_app="nova-api-mysql-router",
                            ),
                            Mock(
                                interface="mysql_client",
                                related_app="nova-cell-mysql-router",
                            ),
                        ]
                    }
                ),
                "nova-mysql-router": router_status(),
                "nova-api-mysql-router": router_status(),
                "nova-cell-mysql-router": router_status(),
            }
        )

        apps, routers = MySQLBackupComponent()._restore_apps(
            jhelper, "nova-mysql", "openstack"
        )

        assert apps == ["nova"]
        assert routers == [
            "nova-api-mysql-router",
            "nova-cell-mysql-router",
            "nova-mysql-router",
        ]

    def test_fails_when_relation_is_missing_from_mysql(self):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone-mysql": Mock(relations={}),
                "keystone-mysql-router": Mock(
                    relations={
                        "database": [
                            Mock(
                                interface="mysql_client",
                                related_app="keystone-mysql",
                            )
                        ]
                    }
                ),
            }
        )

        with pytest.raises(JujuException) as exc:
            MySQLBackupComponent()._restore_apps(jhelper, "keystone-mysql", "openstack")

        assert "router applications" in str(exc.value)

    def test_ignores_cross_model_router_consumers(self):
        jhelper = Mock()

        def router_status(consumer):
            return Mock(
                relations={
                    "database": [
                        Mock(interface="mysql_client", related_app="cinder-mysql"),
                        Mock(interface="mysql_client", related_app=consumer),
                    ]
                }
            )

        jhelper.get_model_status.return_value = Mock(
            apps={
                "cinder": Mock(relations={}),
                "cinder-mysql": Mock(
                    relations={
                        "database": [
                            Mock(
                                interface="mysql_client",
                                related_app="cinder-mysql-router",
                            ),
                            Mock(
                                interface="mysql_client",
                                related_app="cinder-volume-mysql-router",
                            ),
                        ]
                    }
                ),
                "cinder-mysql-router": router_status("cinder"),
                "cinder-volume-mysql-router": router_status("cinder-volume"),
            }
        )

        apps, routers = MySQLBackupComponent()._restore_apps(
            jhelper, "cinder-mysql", "openstack"
        )

        assert apps == ["cinder"]
        assert routers == [
            "cinder-mysql-router",
            "cinder-volume-mysql-router",
        ]

    def test_fails_when_per_service_router_cannot_be_resolved(self):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock(
            apps={"keystone-mysql": Mock(relations={})}
        )

        with pytest.raises(JujuException) as exc:
            MySQLBackupComponent()._restore_apps(jhelper, "keystone-mysql", "openstack")

        assert "router applications" in str(exc.value)

    def test_fails_when_shared_mysql_mapping_cannot_be_resolved(self):
        jhelper = Mock()
        jhelper.get_model_status.return_value = Mock(apps={"mysql": Mock(relations={})})

        with pytest.raises(JujuException) as exc:
            MySQLBackupComponent()._restore_apps(jhelper, "mysql", "openstack")

        assert "Could not resolve router applications" in str(exc.value)


def _finished_backup_action(unit, model, action, params=None, timeout=None):
    if action == "list-backups":
        return {
            "backups": (
                "backup-id | backup-type | backup-status\n"
                "---------------------------------------\n"
                "2026-07-15T00:00:00Z | physical | finished"
            )
        }
    return {}


def _set_per_service_mysql_status(jhelper, app="keystone-mysql"):
    api_app = app.removesuffix("-mysql")
    router_app = f"{api_app}-mysql-router"
    jhelper.get_model_status.return_value = Mock(
        apps={
            app: Mock(
                relations={
                    "database": [Mock(interface="mysql_client", related_app=router_app)]
                }
            ),
            router_app: Mock(
                relations={
                    "database": [
                        Mock(interface="mysql_client", related_app=app),
                        Mock(interface="mysql_client", related_app=api_app),
                    ]
                }
            ),
        }
    )


class TestMySQLRestorePlan:
    def test_scales_router_down_before_restore_and_up_before_resume(self):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_application.return_value = Mock(
            units={"app/0": Mock(), "app/1": Mock()}
        )
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "restore"
        )

        plan = MySQLBackupComponent().build_restore_plan(
            jhelper, target, restore_to_time=None, timeout=120, model="openstack"
        )

        assert [type(step) for step in plan] == [
            _PauseAppStep,
            _ScaleAppStep,
            _ScaleAppStep,
            _RestoreAppStep,
            _ScaleAppStep,
            _ScaleAppStep,
            _ResumeAppStep,
        ]
        scale_targets = [
            (step.application, step.scale)
            for step in plan
            if isinstance(step, _ScaleAppStep)
        ]
        assert scale_targets == [
            ("keystone-mysql-router", 0),
            ("keystone-mysql", 1),
            ("keystone-mysql", 2),
            ("keystone-mysql-router", 2),
        ]

    def test_revert_restores_router_before_resuming_api(self):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_application.return_value = Mock(
            units={"app/0": Mock(), "app/1": Mock()}
        )
        target = ActionTarget(
            "keystone-mysql", "keystone-mysql/0", MYSQL_CHARM, "restore"
        )

        plan = MySQLBackupComponent().build_restore_revert_plan(
            jhelper, target, timeout=120, model="openstack"
        )

        assert [type(step) for step in plan] == [
            _ScaleAppStep,
            _ScaleAppStep,
            _ResumeAppStep,
        ]
        assert plan[1].application == "keystone-mysql-router"
        assert plan[1].scale == 2


class TestRestoreStepWrapper:
    def test_prechecks_all_before_any_restore_work(self, step_context):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.return_value = Mock(
            units={"a/0": Mock(), "a/1": Mock()}
        )
        jhelper.get_application_actions.return_value = []  # no pause/resume
        jhelper.run_action.side_effect = _finished_backup_action

        discovered = {MYSQL_CHARM: ["keystone-mysql"]}

        result = RestoreStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "pause/resume" in result.message
        jhelper.scale_application.assert_not_called()

    def test_restores_each_target(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.return_value = Mock(
            units={"a/0": Mock(), "a/1": Mock()}
        )
        jhelper.get_application_actions.return_value = ["pause", "resume"]

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "list-backups":
                return {"backup-ids": '["vault-backup-openstack-2026-07-15-00-03-28"]'}
            return {}

        jhelper.run_action.side_effect = _run_action

        discovered = {VAULT_CHARM: ["vault"]}

        result = RestoreStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert result.message[0].success is True

    def test_fails_when_restore_target_cannot_be_resolved(self, step_context):
        jhelper = Mock()

        def _leader(app, model):
            if app == "broken-vault":
                raise LeaderNotFoundException("no leader")
            return f"{app}/0"

        jhelper.get_leader_unit.side_effect = _leader

        discovered = {VAULT_CHARM: ["broken-vault"]}

        result = RestoreStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "Could not resolve restore target for broken-vault" in result.message

    def test_reverts_mysql_on_restore_failure(self, step_context):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.return_value = Mock(
            units={"a/0": Mock(), "a/1": Mock()}
        )
        jhelper.get_application_actions.return_value = ["pause", "resume"]

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "list-backups":
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | finished"
                    )
                }
            if action == "restore":
                raise JujuException("restore failed")
            return {}

        jhelper.run_action.side_effect = _run_action

        discovered = {
            MYSQL_CHARM: ["keystone-mysql"],
            VAULT_CHARM: ["vault"],
        }

        result = RestoreStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        outcome = result.message[0]
        assert outcome.success is False
        assert outcome.reverted is True
        assert result.message[1].app == "vault"
        assert "not attempted" in result.message[1].error
        assert not any(
            call.args[2] == "restore-backup"
            for call in jhelper.run_action.call_args_list
        )
        # scale down to 1, then revert scale back up
        scale_calls = [call.args for call in jhelper.scale_application.call_args_list]
        assert ("openstack", "keystone-mysql", 1) in scale_calls

    def test_rollback_attempts_resume_after_scale_failure(self, step_context):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.return_value = Mock(
            units={"keystone-mysql/0": Mock(), "keystone-mysql/1": Mock()}
        )
        jhelper.get_application_actions.return_value = ["pause", "resume"]

        def _scale(model, app, scale):
            if app == "keystone-mysql" and scale == 2:
                raise JujuException("scale failed")

        jhelper.scale_application.side_effect = _scale

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "list-backups":
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | finished"
                    )
                }
            if action == "restore":
                raise JujuException("restore failed")
            return {}

        jhelper.run_action.side_effect = _run_action

        result = RestoreStep(
            jhelper,
            {MYSQL_CHARM: ["keystone-mysql"], VAULT_CHARM: ["vault"]},
        ).run(step_context)

        outcome = result.message[0]
        assert outcome.success is False
        assert outcome.reverted is False
        assert "restore failed" in outcome.error
        assert "scale failed" in outcome.rollback_error
        assert any(
            call.args[2] == "resume" for call in jhelper.run_action.call_args_list
        )
        assert not any(
            call.args[2] == "restore-backup"
            for call in jhelper.run_action.call_args_list
        )
        assert result.message[1].app == "vault"
        assert "not attempted" in result.message[1].error

    def test_scale_read_failure_stops_all_restore_work(self, step_context):
        jhelper = Mock()
        _set_per_service_mysql_status(jhelper)
        jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
        jhelper.get_application.side_effect = ApplicationNotFoundException("missing")
        jhelper.get_application_actions.return_value = ["pause", "resume"]

        result = RestoreStep(
            jhelper,
            {MYSQL_CHARM: ["keystone-mysql"], VAULT_CHARM: ["vault"]},
        ).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "Could not read current scale" in result.message
        jhelper.run_action.assert_not_called()

    def test_precheck_plan_construction_failure_returns_failed_result(
        self, step_context
    ):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "mysql/0"
        jhelper.get_model_status.return_value = Mock(apps={"mysql": Mock(relations={})})

        result = RestoreStep(jhelper, {MYSQL_CHARM: ["mysql"]}).run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "Could not resolve router applications" in result.message
