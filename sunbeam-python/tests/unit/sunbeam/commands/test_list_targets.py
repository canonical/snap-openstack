# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from rich.console import Console

from sunbeam.commands.backup_restore import backup_group
from sunbeam.core.juju import JujuException


def _app_status(charm_name, status="active", interface="s3"):
    app = Mock()
    app.charm_name = charm_name
    app.units = {}
    app.app_status.current = status
    app.relations = {"s3-parameters": [Mock(interface=interface)]} if interface else {}
    return app


def _target_rows(output):
    return [
        [cell.strip() for cell in line.split("│")[1:-1]]
        for line in output.splitlines()
        if line.startswith("│")
    ]


@pytest.fixture
def deployment(monkeypatch):
    monkeypatch.setattr("sunbeam.commands.backup_restore.console", Console(width=160))
    return Mock()


@pytest.fixture
def jhelper(deployment):
    helper = deployment.get_juju_helper.return_value
    yield helper
    helper.run_action.assert_not_called()
    helper.scale_application.assert_not_called()
    deployment.get_tfhelper.assert_not_called()
    deployment.get_client.assert_not_called()


class TestListTargetsCommand:
    def test_lists_only_supported_s3_related_targets(self, deployment, jhelper):
        jhelper.get_model_status.return_value = Mock(
            apps={
                "keystone-mysql": _app_status("mysql-k8s"),
                "vault": _app_status("vault-k8s"),
                "unrelated-mysql": _app_status("mysql-k8s", interface=None),
                "wrong-interface-vault": _app_status("vault-k8s", interface="database"),
                "unsupported": _app_status("postgresql-k8s"),
            }
        )
        jhelper.get_relation_map.side_effect = lambda app, endpoint, model: {
            "1": f"{app}-s3"
        }

        result = CliRunner().invoke(backup_group, ["target", "list"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert _target_rows(result.output) == [
            ["keystone-mysql", "mysql-k8s", "keystone-mysql-s3", "Ready"],
            ["vault", "vault-k8s", "vault-s3", "Ready"],
        ]

    @pytest.mark.parametrize("charm_name", ["mysql-k8s", "vault-k8s"])
    @pytest.mark.parametrize("status", ["blocked", "waiting"])
    def test_inactive_s3_target_remains_visible(
        self, deployment, jhelper, charm_name, status
    ):
        jhelper.get_model_status.return_value = Mock(
            apps={"inactive-target": _app_status(charm_name, status=status)}
        )
        jhelper.get_relation_map.return_value = {"1": "backup-s3"}

        result = CliRunner().invoke(backup_group, ["target", "list"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert _target_rows(result.output) == [
            ["inactive-target", charm_name, "backup-s3", "Not active"]
        ]

    @pytest.mark.parametrize("lookup_fails", [False, True])
    def test_unresolved_integration_keeps_configured_target(
        self, deployment, jhelper, lookup_fails
    ):
        jhelper.get_model_status.return_value = Mock(
            apps={"keystone-mysql": _app_status("mysql-k8s")}
        )
        if lookup_fails:
            jhelper.get_relation_map.side_effect = JujuException("lookup failed")
        else:
            jhelper.get_relation_map.return_value = {}

        result = CliRunner().invoke(backup_group, ["target", "list"], obj=deployment)

        assert result.exit_code == 0, result.output
        assert _target_rows(result.output) == [
            ["keystone-mysql", "mysql-k8s", "Unknown", "Ready"]
        ]

    @pytest.mark.parametrize("has_unrelated_app", [False, True])
    def test_no_s3_targets_displays_empty_table(
        self, deployment, jhelper, has_unrelated_app
    ):
        apps = (
            {"keystone-mysql": _app_status("mysql-k8s", interface=None)}
            if has_unrelated_app
            else {}
        )
        jhelper.get_model_status.return_value = Mock(apps=apps)

        result = CliRunner().invoke(backup_group, ["target", "list"], obj=deployment)

        assert result.exit_code == 0, result.output
        for header in ("Application", "Component", "S3 integration", "Readiness"):
            assert header in result.output
        assert _target_rows(result.output) == []
