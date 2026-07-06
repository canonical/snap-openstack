# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

from sunbeam.core.common import ResultType
from sunbeam.core.juju import JujuException
from sunbeam.steps.backup import BackupTarget
from sunbeam.steps.restore import (
    PauseControlPlaneStep,
    RestoreMySQLStep,
    RestoreVaultStep,
    ResumeControlPlaneStep,
    ScaleMySQLStep,
)


class TestGuardedSteps:
    def test_pause_returns_failed(self, step_context):
        result = PauseControlPlaneStep(Mock()).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "Unimplemented" in result.message

    def test_resume_returns_failed(self, step_context):
        result = ResumeControlPlaneStep(Mock()).run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_restore_mysql_returns_failed_and_does_not_dispatch(self, step_context):
        jhelper = Mock()
        target = BackupTarget(
            "keystone-mysql", "keystone-mysql/0", "mysql", "restore", 3
        )
        result = RestoreMySQLStep(jhelper, target).run(step_context)
        assert result.result_type == ResultType.FAILED
        jhelper.run_action.assert_not_called()

    def test_restore_vault_surfaces_prerequisite(self, step_context):
        result = RestoreVaultStep(Mock()).run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "Unimplemented" in result.message


class TestScaleMySQLStep:
    def test_scales_and_waits(self, step_context):
        jhelper = Mock()
        result = ScaleMySQLStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.scale_application.assert_called_once_with(
            "openstack", "keystone-mysql", 1
        )
        jhelper.wait_until_active.assert_called_once()

    def test_returns_failed_on_juju_error(self, step_context):
        jhelper = Mock()
        jhelper.scale_application.side_effect = JujuException("boom")
        result = ScaleMySQLStep(jhelper, "keystone-mysql", 1).run(step_context)
        assert result.result_type == ResultType.FAILED
