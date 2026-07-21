# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from sunbeam.commands.backup_restore import restore
from sunbeam.core.juju import (
    ActionFailedException,
    JujuException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL


def _app_status(charm_name):
    app = Mock()
    app.charm_name = charm_name
    app.units = {}
    app.relations = {}
    app.app_status.current = "active"
    return app


def _model_status(apps):
    apps = dict(apps)
    for app_name, app_status in list(apps.items()):
        if app_status.charm_name != "mysql-k8s" or not app_name.endswith("-mysql"):
            continue
        api_app = app_name.removesuffix("-mysql")
        router_app = f"{api_app}-mysql-router"
        app_status.relations["database"] = [
            Mock(interface="mysql_client", related_app=router_app)
        ]
        apps.setdefault(
            router_app,
            Mock(
                charm_name="mysql-router-k8s",
                relations={
                    "database": [
                        Mock(interface="mysql_client", related_app=app_name),
                        Mock(interface="mysql_client", related_app=api_app),
                    ]
                },
            ),
        )
    status = Mock()
    status.apps = apps
    return status


def _s3_related(app):
    relation = Mock()
    relation.interface = "s3"
    app.relations = {"s3-parameters": [relation]}
    return app


def _default_run_action(unit, model, action, params=None, timeout=None):
    if action == "get-cluster-status":
        return {
            "status": {
                "defaultreplicaset": {
                    "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                }
            }
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
    mysql = _s3_related(_app_status("mysql-k8s"))
    vault = _s3_related(_app_status("vault-k8s"))
    jhelper.get_model_status.return_value = _model_status(
        {
            "keystone-mysql": mysql,
            "vault": vault,
            "keystone-k8s": _app_status("keystone-k8s"),
        }
    )
    jhelper.get_application.return_value = _app_status("mysql-k8s")
    jhelper.get_application_actions.return_value = ["pause", "resume"]
    jhelper.run_action.side_effect = _default_run_action
    return jhelper


class TestRestoreCommand:
    def test_stops_at_pause_guard_and_is_non_destructive(self, deployment, jhelper):
        jhelper.get_application_actions.return_value = []
        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "pause/resume" in result.output
        jhelper.scale_application.assert_not_called()

    def test_prechecks_pause_resume_for_all_apps_before_any_restore_work(
        self, deployment, jhelper
    ):
        mysql_a = _s3_related(_app_status("mysql-k8s"))
        mysql_b = _s3_related(_app_status("mysql-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql_a, "nova-mysql": mysql_b}
        )

        def _get_actions(app, model):
            if app == "nova":
                return []
            return ["pause", "resume"]

        jhelper.get_application_actions.side_effect = _get_actions

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
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
        assert "nova" in result.output
        jhelper.scale_application.assert_not_called()

        restore_actions = {
            call.args[2]
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2
        }
        assert "pause" not in restore_actions
        assert "resume" not in restore_actions
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
        mysql = _app_status("mysql-k8s")  # no s3
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
                "keystone-k8s": _app_status("keystone-k8s"),
            }
        )

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "is not ready for backup" in result.output
        jhelper.scale_application.assert_not_called()

    def test_no_supported_apps_left(self, deployment, jhelper):
        mysql = _app_status("mysql-k8s")  # no s3
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert (
            "No applications remain to restore after validation. Exiting."
            in result.output
        )

    def test_unrelated_vault_is_skipped_and_restore_continues(
        self, deployment, jhelper
    ):
        mysql = _s3_related(_app_status("mysql-k8s"))
        vault = _app_status("vault-k8s")  # no s3
        jhelper.get_model_status.return_value = _model_status(
            {
                "keystone-mysql": mysql,
                "vault": vault,
                "keystone-k8s": _app_status("keystone-k8s"),
            }
        )

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "vault is not ready for backup" in result.output

    def test_pitr_falls_back_to_latest_for_components_without_pitr_support(
        self, deployment, jhelper
    ):
        result = CliRunner().invoke(
            restore,
            ["--restore-to-time", "2026-07-15 00:00:00", "--no-prompt"],
            obj=deployment,
        )

        assert result.exit_code == 0, result.output
        assert "vault does not support --restore-to-time." in result.output
        assert "Restoring latest available" in result.output
        assert "backup instead." in result.output
        restore_calls = [
            call
            for call in jhelper.run_action.call_args_list
            if len(call.args) > 2 and call.args[2] == "restore-backup"
        ]
        assert len(restore_calls) == 1
        assert restore_calls[0].args[3] == {
            "backup-id": "vault-backup-openstack-2026-07-15-00-03-28"
        }

    def test_no_backups_found(self, deployment, jhelper):
        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
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
        assert (
            "No applications remain to restore after validation. Exiting."
            in result.output
        )

    def test_warns_when_some_backups_failed_but_restore_continues(
        self, deployment, jhelper
    ):
        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            if action == "list-backups" and unit.startswith("keystone-mysql"):
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | failed\n"
                        "2026-07-14T00:00:00Z | physical | finished"
                    )
                }
            if action == "list-backups" and unit.startswith("vault"):
                return {
                    "backup-ids": json.dumps(
                        ["vault-backup-openstack-2026-07-15-00-03-28"]
                    )
                }
            return {}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert "Some backups for keystone-mysql failed." in result.output
        assert "Only successful backups will be" in result.output
        assert "considered for restore" in result.output

    def test_partial_backup_failures_prompt_and_decline_aborts(
        self, deployment, jhelper, monkeypatch
    ):
        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            if action == "list-backups" and unit.startswith("keystone-mysql"):
                return {
                    "backups": (
                        "backup-id | backup-type | backup-status\n"
                        "---------------------------------------\n"
                        "2026-07-15T00:00:00Z | physical | failed\n"
                        "2026-07-14T00:00:00Z | physical | finished"
                    )
                }
            if action == "list-backups" and unit.startswith("vault"):
                return {
                    "backup-ids": json.dumps(
                        ["vault-backup-openstack-2026-07-15-00-03-28"]
                    )
                }
            return {}

        jhelper.run_action.side_effect = _run_action

        monkeypatch.setattr(
            "sunbeam.commands.backup_restore.ConfirmQuestion.ask",
            lambda self, *a, **k: False,
        )

        result = CliRunner().invoke(restore, obj=deployment)

        assert result.exit_code == 1, result.output
        assert "Aborted" in result.output

    def test_inventory_lookup_failures_are_reported_and_exit_2(
        self, deployment, jhelper
    ):
        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
                }
            if action == "list-backups":
                raise ActionFailedException("list failed")
            return {}

        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Failed to list backups for" in result.output

    def test_force_proceeds_when_target_app_is_inactive(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        mysql.app_status.current = "blocked"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
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

        result = CliRunner().invoke(restore, ["--force", "--no-prompt"], obj=deployment)

        assert result.exit_code == 0, result.output
        restore_calls = [
            call
            for call in jhelper.run_action.call_args_list
            if call.args[2] == "restore"
        ]
        assert restore_calls

    def test_force_does_not_bypass_inventory_target_resolution_failure(
        self, deployment, jhelper
    ):
        mysql = _s3_related(_app_status("mysql-k8s"))
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        leader_calls = {"keystone-mysql": 0}

        def _leader(app, model):
            if app != "keystone-mysql":
                return f"{app}/0"
            leader_calls[app] += 1
            if leader_calls[app] == 1:
                raise LeaderNotFoundException("temporary leader lookup failure")
            return "keystone-mysql/0"

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
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

        jhelper.get_leader_unit.side_effect = _leader
        jhelper.run_action.side_effect = _run_action

        result = CliRunner().invoke(restore, ["--force", "--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Could not resolve restore target for keystone-mysql" in result.output
        assert not any(
            call.args[2] == "restore" for call in jhelper.run_action.call_args_list
        )

    def test_mixed_unresolved_target_stops_restore(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        vault = _s3_related(_app_status("vault-k8s"))
        jhelper.get_model_status.return_value = _model_status(
            {"keystone-mysql": mysql, "vault": vault}
        )

        def _leader(app, model):
            if app == "vault":
                raise LeaderNotFoundException("no vault leader")
            return f"{app}/0"

        jhelper.get_leader_unit.side_effect = _leader
        jhelper.run_action.side_effect = _default_run_action

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "Could not resolve restore target for vault" in result.output
        jhelper.scale_application.assert_not_called()
        assert not any(
            call.args[2] in {"restore", "restore-backup"}
            for call in jhelper.run_action.call_args_list
        )

    def test_force_does_not_bypass_missing_pause_resume_actions(
        self, deployment, jhelper
    ):
        jhelper.get_application_actions.return_value = []

        result = CliRunner().invoke(restore, ["--force", "--no-prompt"], obj=deployment)

        assert result.exit_code == 1, result.output
        assert "pause/resume" in result.output
        jhelper.scale_application.assert_not_called()

    def test_non_active_target_app_is_skipped(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        mysql.app_status.current = "error"
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})

        result = CliRunner().invoke(restore, ["--no-prompt"], obj=deployment)

        assert result.exit_code == 2, result.output
        assert "keystone-mysql" in result.output

    def test_mysql_restore_failure_reverts_and_reports(self, deployment, jhelper):
        mysql = _s3_related(_app_status("mysql-k8s"))
        mysql.units = {"keystone-mysql/0": Mock(), "keystone-mysql/1": Mock()}
        jhelper.get_model_status.return_value = _model_status({"keystone-mysql": mysql})
        jhelper.get_application.return_value = mysql

        def _run_action(unit, model, action, params=None, timeout=None):
            if action == "get-cluster-status":
                return {
                    "status": {
                        "defaultreplicaset": {
                            "topology": {"mysql-0": {"memberrole": "PRIMARY"}}
                        }
                    }
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

        assert result.exit_code == 2, result.output
        assert "keystone-mysql" in result.output
        assert "restore failed" in result.output
        assert [call.args for call in jhelper.scale_application.call_args_list] == [
            (OPENSTACK_MODEL, "keystone-mysql-router", 0),
            (OPENSTACK_MODEL, "keystone-mysql", 1),
            (OPENSTACK_MODEL, "keystone-mysql", 2),
            (OPENSTACK_MODEL, "keystone-mysql-router", 2),
        ]
