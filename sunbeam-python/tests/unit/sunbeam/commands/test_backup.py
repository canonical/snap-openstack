# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup import backup


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
    return jhelper


def _leader_only_cluster_status(unit, model, action, params=None, timeout=None):
    if action == "get-cluster-status":
        return {
            "status": json.dumps(
                {
                    "defaultreplicaset": {
                        "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                    }
                }
            )
        }
    return {"backup-id": f"backup-{unit.replace('/', '-')}"}


class TestBackupCommand:
    def test_prompt_abort_stops_before_work(self, deployment, jhelper):
        result = CliRunner().invoke(backup, obj=deployment, input="n\n")

        assert result.exit_code == 1, result.output
        assert "Aborted" in result.output
        jhelper.get_model_status.assert_not_called()

    def test_no_applications_exits_2(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status({})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Nothing to back up" in result.output

    def test_all_success_exits_0(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": _app_status("mysql-k8s"),
                "vault": _app_status("vault-k8s"),
            }
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")
        jhelper.run_action.side_effect = _leader_only_cluster_status

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "2 succeeded, 0 failed" in result.output

    def test_partial_failure_exits_1_with_warning(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": _app_status("mysql-k8s"),
                "vault": _app_status("vault-k8s"),
            }
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": json.dumps(
                        {
                            "defaultreplicaset": {
                                "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                            }
                        }
                    )
                }
            if unit == "keystone-mysql/0":
                raise Exception("backup failed")
            return {"backup-id": "backup-vault-0"}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "1 succeeded, 1 failed" in result.output
        assert "Warning" in result.output

    def test_all_failed_exits_2(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": _app_status("mysql-k8s")}
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": json.dumps(
                        {
                            "defaultreplicaset": {
                                "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                            }
                        }
                    )
                }
            raise Exception("backup failed")

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "0 succeeded, 1 failed" in result.output
