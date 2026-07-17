# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import dataclasses
from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import JujuException
from sunbeam.steps.backup_restore import (
    MYSQL_CHARM,
    VAULT_CHARM,
    ActionTarget,
    RestoreStep,
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
    def test_pause_dispatches_action_without_is_skip_check(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-k8s/0"

        step = _PauseAppStep(jhelper, app="keystone-k8s")
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.get_application_actions.assert_not_called()
        jhelper.run_action.assert_called_once()

    def test_resume_dispatches_action(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-k8s/0"

        step = _ResumeAppStep(jhelper, app="keystone-k8s")
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once()

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
        component = dataclasses.replace(
            _mysql_component(), restore_to_time_param="restore-to-time"
        )
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


class TestScaleMySQLStep:
    def test_scales_and_waits(self, step_context):
        jhelper = Mock()
        result = _ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.scale_application.assert_called_once_with(
            "openstack", "keystone-mysql", 1
        )
        jhelper.wait_until_active.assert_called_once()

    def test_returns_failed_on_juju_error(self, step_context):
        jhelper = Mock()
        jhelper.scale_application.side_effect = JujuException("boom")
        result = _ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.FAILED


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


class TestRestoreStepWrapper:
    def test_prechecks_all_before_any_restore_work(self, step_context):
        jhelper = Mock()
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

    def test_reverts_mysql_on_restore_failure(self, step_context):
        jhelper = Mock()
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

        discovered = {MYSQL_CHARM: ["keystone-mysql"]}

        result = RestoreStep(jhelper, discovered).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        outcome = result.message[0]
        assert outcome.success is False
        assert outcome.reverted is True
        # scale down to 1, then revert scale back up
        scale_calls = [call.args for call in jhelper.scale_application.call_args_list]
        assert ("openstack", "keystone-mysql", 1) in scale_calls
