# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.restore import restore


def _app_status(charm_name):
    app = Mock()
    app.charm_name = charm_name
    app.units = {}
    return app


def _model_status(apps):
    status = Mock()
    status.apps = apps
    return status


@pytest.fixture
def deployment():
    return Mock()


@pytest.fixture
def jhelper(deployment):
    jhelper = Mock()
    deployment.get_juju_helper.return_value = jhelper
    jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
    jhelper.get_model_status.return_value = _model_status(
        {"keystone-mysql": _app_status("mysql-k8s"), "vault": _app_status("vault-k8s")}
    )
    jhelper.get_application.return_value = _app_status("mysql-k8s")
    return jhelper


class TestRestoreCommand:
    def test_prompt_abort_stops_before_work(self, deployment, jhelper):
        result = CliRunner().invoke(restore, obj=deployment, input="n\n")

        assert result.exit_code == 1, result.output
        assert "Aborted" in result.output
        jhelper.get_model_status.assert_not_called()
        jhelper.scale_application.assert_not_called()

    def test_stops_at_pause_guard_and_is_non_destructive(self, deployment, jhelper):
        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "Unimplemented" in result.output
        jhelper.scale_application.assert_not_called()

    def test_no_restore_or_resume_action_dispatched(self, deployment, jhelper):
        CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        for call in jhelper.run_action.call_args_list:
            action = call.args[2] if len(call.args) > 2 else ""
            assert action not in ("restore-backup", "resume")

    def test_invalid_restore_to_time_fails_fast(self, deployment, jhelper):
        result = CliRunner().invoke(
            restore, ["--restore-to-time", "not-a-date"], obj=deployment
        )

        assert result.exit_code != 0
        assert "YYYY-MM-DD HH:MM:SS" in result.output
        jhelper.get_model_status.assert_not_called()
        jhelper.scale_application.assert_not_called()
