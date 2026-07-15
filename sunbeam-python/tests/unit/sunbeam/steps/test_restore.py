# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import JujuException
from sunbeam.steps.backup_restore import (
    BackupTarget,
    PauseAppStep,
    RestoreMySQLStep,
    RestoreVaultStep,
    ResumeAppStep,
    ScaleAppStep,
)


class TestGuardedSteps:
    def test_pause_dispatches_action_without_is_skip_check(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-k8s/0"

        step = PauseAppStep(jhelper, app="keystone-k8s")
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        jhelper.get_application_actions.assert_not_called()
        jhelper.run_action.assert_called_once()

    def test_resume_dispatches_action(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-k8s/0"

        step = ResumeAppStep(jhelper, app="keystone-k8s")
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
        target = BackupTarget(
            "keystone-mysql", "keystone-mysql/0", "mysql", "restore", 3
        )
        result = RestoreMySQLStep(jhelper, target).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_args_list[1].args[2] == "restore"
        assert jhelper.run_action.call_args_list[1].args[3] == {
            "backup-id": "2026-07-15T00:00:00Z"
        }

    def test_restore_mysql_uses_restore_to_time(self, step_context):
        jhelper = Mock()
        jhelper.get_leader_unit.return_value = "keystone-mysql/0"
        target = BackupTarget(
            "keystone-mysql", "keystone-mysql/0", "mysql", "restore", 3
        )

        result = RestoreMySQLStep(
            jhelper,
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
        target = BackupTarget("vault", "vault/0", "vault", "restore-backup", 1)

        result = RestoreVaultStep(jhelper, target).run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert jhelper.run_action.call_args_list[1].args[2] == "restore-backup"
        assert jhelper.run_action.call_args_list[1].args[3] == {
            "backup-id": "vault-backup-openstack-2026-07-15-00-03-28"
        }


class TestScaleMySQLStep:
    def test_scales_and_waits(self, step_context):
        jhelper = Mock()
        result = ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.scale_application.assert_called_once_with(
            "openstack", "keystone-mysql", 1
        )
        jhelper.wait_until_active.assert_called_once()

    def test_returns_failed_on_juju_error(self, step_context):
        jhelper = Mock()
        jhelper.scale_application.side_effect = JujuException("boom")
        result = ScaleAppStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.FAILED
