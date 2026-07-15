# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup_restore import list_backups


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


class TestListBackupsCommand:
    def test_no_applications(self, deployment, jhelper):
        jhelper.get_model_status.return_value = _model_status({})

        result = CliRunner().invoke(list_backups, obj=deployment)

        assert result.exit_code == 2, result.output
        assert "No applications found" in result.output

    def test_no_supported_apps_left(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.relations = {}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(list_backups, obj=deployment)

        assert result.exit_code == 2, result.output
        assert "the following MySQL applications are not related" in result.output
        assert "No applications found to list backups from. Exiting." in result.output

    def test_lists_backups_and_writes_manifest(self, deployment, jhelper):
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
        jhelper.get_application.side_effect = lambda app, model: (
            mysql if app == "keystone-mysql" else vault
        )

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
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | finished"
                    )
                }
            return {
                "backup-ids": json.dumps(["vault-backup-openstack-2026-07-15-00-03-28"])
            }

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(list_backups, obj=deployment)

        assert result.exit_code == 0, result.output
        assert "keystone-mysql" in result.output
        assert "vault" in result.output
        assert "Backup inventory manifest written to" in result.output

    def test_non_active_target_app_fails(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")
        mysql.app_status.current = "waiting"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(list_backups, obj=deployment)

        assert result.exit_code == 1, result.output
        assert "keystone-mysql" in result.output
        assert "waiting" in result.output

    def test_list_action_failures_exit_2_with_details(self, deployment, jhelper):
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
        jhelper.get_application.side_effect = lambda app, model: (
            mysql if app == "keystone-mysql" else vault
        )

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

        result = CliRunner().invoke(list_backups, obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Failed to list backups for" in result.output
        assert "keystone-mysql" in result.output
        assert "vault" in result.output
