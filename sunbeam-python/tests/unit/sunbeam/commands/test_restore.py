# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup_restore import restore
from sunbeam.core.juju import JujuException


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


def _default_list_backups(unit, model, action, params=None, timeout=None):
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
    if action == "list-backups" and unit.startswith("keystone-mysql"):
        return {
            "backups": (
                "backup-id | backup-type | backup-status\n"
                "---------------------------------------\n"
                "2026-07-15T00:00:00Z | physical | finished"
            )
        }
    if action == "list-backups" and unit.startswith("vault"):
        return {
            "backup-ids": json.dumps(["vault-backup-openstack-2026-07-15-00-03-28"])
        }
    return {}


@pytest.fixture
def deployment():
    return Mock()


@pytest.fixture
def jhelper(deployment):
    jhelper = Mock()
    deployment.get_juju_helper.return_value = jhelper
    jhelper.get_leader_unit.side_effect = lambda app, model: f"{app}/0"
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
            "keystone-k8s": _app_status("keystone-k8s"),
        }
    )
    jhelper.get_application.return_value = _app_status("mysql-k8s")
    jhelper.get_application_actions.return_value = ["pause", "resume"]
    jhelper.run_action.side_effect = _default_list_backups
    return jhelper


class TestRestoreCommand:
    def test_prompt_abort_stops_before_work(self, deployment, jhelper):
        result = CliRunner().invoke(restore, obj=deployment, input="n\n")

        assert result.exit_code == 1, result.output
        assert "Aborted" in result.output
        jhelper.scale_application.assert_not_called()
        restore_actions = {
            call.args[2]
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2
        }
        assert "restore-backup" not in restore_actions
        assert "restore" not in restore_actions

    def test_stops_at_pause_guard_and_is_non_destructive(self, deployment, jhelper):
        jhelper.get_application_actions.return_value = []
        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "pause/resume" in result.output
        jhelper.scale_application.assert_not_called()

    def test_prechecks_pause_resume_for_all_apps_before_any_restore_work(
        self, deployment, jhelper
    ):
        mysql_a = _app_status("mysql-k8s")
        mysql_a_s3 = Mock()
        mysql_a_s3.interface = "s3"
        mysql_a.relations = {"s3-parameters": [mysql_a_s3]}

        mysql_b = _app_status("mysql-k8s")
        mysql_b_s3 = Mock()
        mysql_b_s3.interface = "s3"
        mysql_b.relations = {"s3-parameters": [mysql_b_s3]}

        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql_a,
                "nova-mysql": mysql_b,
            }
        )

        def _get_actions(app, model):
            if app == "nova-mysql":
                return ["pause"]
            return ["pause", "resume"]

        jhelper.get_application_actions.side_effect = _get_actions

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
            if action == "list-backups":
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | finished"
                    )
                }
            return {}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "pause/resume" in result.output
        assert "nova-mysql" in result.output
        jhelper.scale_application.assert_not_called()

        restore_actions = {
            call.args[2]
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2
        }
        assert "pause" not in restore_actions
        assert "resume" not in restore_actions
        assert "restore" not in restore_actions

    def test_no_restore_or_resume_action_dispatched(self, deployment, jhelper):
        jhelper.get_application_actions.return_value = []
        CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        restore_actions = {
            call.args[2]
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2
        }
        assert "restore-backup" not in restore_actions
        assert "restore" not in restore_actions

    def test_invalid_restore_to_time_fails_fast(self, deployment, jhelper):
        result = CliRunner().invoke(
            restore, ["--restore-to-time", "not-a-date"], obj=deployment
        )

        assert result.exit_code != 0
        assert "YYYY-MM-DD HH:MM:SS" in result.output
        jhelper.get_model_status.assert_not_called()
        jhelper.scale_application.assert_not_called()

    def test_unrelated_mysql_is_skipped_and_restore_continues(
        self, deployment, jhelper
    ):
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
                "keystone-k8s": _app_status("keystone-k8s"),
            }
        )

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "will be skipped" in result.output
        jhelper.scale_application.assert_not_called()

    def test_no_supported_apps_left(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.relations = {}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No restore targets could be resolved. Exiting." in result.output

    def test_unrelated_vault_is_skipped_and_restore_continues(
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
                "keystone-k8s": _app_status("keystone-k8s"),
            }
        )

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "the following Vault applications are not related" in result.output

    def test_warns_on_pitr_for_vault(self, deployment, jhelper):
        result = CliRunner().invoke(
            restore,
            ["--restore-to-time", "2026-07-15 00:00:00", "--no-prompt"],
            obj=deployment,
        )

        assert result.exit_code == 0, result.output
        assert "Vault does not support point-in-time restore" in result.output

    def test_no_supported_apps_left_when_only_vault(self, deployment, jhelper):
        vault = _app_status("vault-k8s")
        vault.relations = {}
        jhelper.get_model_status.return_value = _model_status({"vault": vault})

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No restore targets could be resolved. Exiting." in result.output

    def test_no_backups_found(self, deployment, jhelper):
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
            if action == "list-backups" and unit.startswith("keystone-mysql"):
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | failed"
                    )
                }
            if action == "list-backups" and unit.startswith("vault"):
                return {"backup-ids": json.dumps([])}
            return {}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No backups were found to restore from. Exiting." in result.output

    def test_inventory_lookup_failures_are_reported_and_exit_2(
        self, deployment, jhelper
    ):
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
            if action == "list-backups":
                raise Exception("list failed")
            return {}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Failed to list backups for" in result.output
        assert "keystone-mysql" in result.output
        assert "vault" in result.output

    def test_non_active_target_app_fails(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.app_status.current = "error"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "keystone-mysql" in result.output
        assert "error" in result.output

    def test_mysql_restore_failure_attempts_cleanup_and_reports_app(
        self, deployment, jhelper
    ):
        mysql = _app_status("mysql-k8s")
        mysql_s3_relation = Mock()
        mysql_s3_relation.interface = "s3"
        mysql.relations = {"s3-parameters": [mysql_s3_relation]}
        mysql.units = {"keystone-mysql/0": Mock(), "keystone-mysql/1": Mock()}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})
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

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "keystone-mysql" in result.output
        assert "restore failed" in result.output
        assert jhelper.scale_application.call_args_list == [
            ((deployment.openstack_machines_model, "keystone-mysql", 1),),
            ((deployment.openstack_machines_model, "keystone-mysql", 2),),
        ]
