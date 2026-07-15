# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup_restore import backup


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

    def test_no_applications(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status({})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No applications found to back up. Exiting." in result.output

    def test_all_success(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql_s3_relation = Mock()
        mysql_s3_relation.interface = "s3"
        mysql.relations = {"s3-parameters": [mysql_s3_relation]}
        vault = _app_status("vault-k8s")
        vault_s3_relation = Mock()
        vault_s3_relation.interface = "s3"
        vault.relations = {"s3-parameters": [vault_s3_relation]}
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
            }
        )
        jhelper.get_application.return_value = _app_status("mysql-k8s")
        jhelper.run_action.side_effect = _leader_only_cluster_status

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "2 succeeded, 0 failed" in result.output

    def test_partial_failure(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql_s3_relation = Mock()
        mysql_s3_relation.interface = "s3"
        mysql.relations = {"s3-parameters": [mysql_s3_relation]}
        vault = _app_status("vault-k8s")
        vault_s3_relation = Mock()
        vault_s3_relation.interface = "s3"
        vault.relations = {"s3-parameters": [vault_s3_relation]}
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
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

    def test_all_failed(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql_s3_relation = Mock()
        mysql_s3_relation.interface = "s3"
        mysql.relations = {"s3-parameters": [mysql_s3_relation]}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})
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

    def test_unrelated_mysql_is_skipped_and_backup_continues(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.relations = {}
        vault = _app_status("vault-k8s")
        vault_s3_relation = Mock()
        vault_s3_relation.interface = "s3"
        vault.relations = {"s3-parameters": [vault_s3_relation]}
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
            }
        )
        jhelper.get_application.return_value = vault
        jhelper.run_action.return_value = {"backup-id": "backup-vault-0"}

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "will be skipped" in result.output
        called_units = [call.args[0] for call in jhelper.run_action.call_args_list]
        assert called_units == ["vault/0"]

    def test_no_supported_apps_left(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.relations = {}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "the following MySQL applications are not related" in result.output
        assert "Could not resolve a backup target for any application" in result.output

    def test_unrelated_vault_is_skipped_and_mysql_backup_continues(
        self, deployment, jhelper
    ):
        mysql = _app_status("mysql-k8s")
        mysql_s3_relation = Mock()
        mysql_s3_relation.interface = "s3"
        mysql.relations = {"s3-parameters": [mysql_s3_relation]}
        vault = _app_status("vault-k8s")
        vault.relations = {}
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
            }
        )
        jhelper.get_application.return_value = mysql

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
            return {"backup-id": "backup-keystone-mysql-0"}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "the following Vault applications are not related" in result.output

    def test_no_supported_apps_left_when_only_vault(self, deployment, jhelper):
        vault = _app_status("vault-k8s")
        vault.relations = {}
        jhelper.get_model_status.return_value = _model_status({"vault": vault})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Could not resolve a backup target for any application" in result.output

    def test_non_active_target_app_fails(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.app_status.current = "blocked"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(backup, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "keystone-mysql" in result.output
        assert "blocked" in result.output
