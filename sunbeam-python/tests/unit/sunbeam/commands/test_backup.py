# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup_restore import backup
from sunbeam.core.juju import ActionFailedException


def _app_status(charm_name):
    app = Mock()
    app.charm_name = charm_name
    app.units = {}
    app.relations = {}
    app.app_status.current = "active"
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
            "status": {
                "defaultreplicaset": {
                    "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                }
            }
        }
    return {"backup-id": f"backup-{unit.replace('/', '-')}"}


def _s3_related(app):
    relation = Mock()
    relation.interface = "s3"
    app.relations = {"s3-parameters": [relation]}
    return app


class TestBackupCommand:
    def test_no_applications(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status({})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No applications found to back up. Exiting." in result.output

    def test_all_success(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql, "vault": vault}
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")
        jhelper.run_action.side_effect = _leader_only_cluster_status

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "2 succeeded, 0 failed" in result.output

    def test_partial_failure(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql, "vault": vault}
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            if unit == "keystone-mysql/0":
                raise ActionFailedException("backup failed")
            return {"backup-id": "backup-vault-0"}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "1 succeeded, 1 failed" in result.output
        assert "Warning" in result.output

    def test_all_failed(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})
        jhelper.get_application.return_value = _app_status("mysql-k8s")

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            raise ActionFailedException("backup failed")

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "0 succeeded, 1 failed" in result.output

    def test_unrelated_mysql_is_skipped_and_backup_continues(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")  # no s3
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql, "vault": vault}
        )
        jhelper.get_application.return_value = vault
        jhelper.run_action.return_value = {"backup-id": "backup-vault-0"}

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "is not ready for backup" in result.output
        assert "keystone-mysql" in result.output

    def test_declining_confirmation_aborts_backup(
        self, deployment, jhelper, monkeypatch
    ):
        mysql = _app_status("mysql-k8s")  # no s3, will be skipped -> prompt
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql, "vault": vault}
        )
        jhelper.get_application.return_value = vault

        monkeypatch.setattr(
            "sunbeam.commands.backup_restore.ConfirmQuestion.ask",
            lambda self, *a, **k: False,
        )

        result = CliRunner().invoke(backup, obj=deployment)

        assert result.exit_code != 0
        assert "Aborted" in result.output
        # No backup action dispatched.
        actions = [
            call.args[2]
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2
        ]
        assert "create-backup" not in actions

    def test_no_supported_apps_left(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")  # no s3
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "keystone-mysql" in result.output
        assert "No applications remain to back up after validation." in result.output

    def test_non_active_target_app_is_skipped(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        mysql.app_status.current = "blocked"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "keystone-mysql" in result.output
        assert "active" in result.output
        assert "No applications remain to back up after validation." in result.output
