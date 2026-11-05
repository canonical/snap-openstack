# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import click
import pytest

from sunbeam.core.common import Result, ResultType
from sunbeam.provider import commands as provider_commands
from sunbeam.provider.maas.steps import (
    MaasBackupClusterdCredentialsStep,
    MaasSaveClusterdCredentialsStep,
)


def test_update_clusterd_credentials_invokes_plan(tmp_path, mocker):
    """Test that the command invokes the plan with expected args."""
    deployment = Mock()
    deployment.juju_controller = Mock()
    run_plan_spy = mocker.patch.object(provider_commands, "run_plan")
    mocker.patch.object(provider_commands, "run_preflight_checks")

    # Act
    cmd = provider_commands.update_clusterd_credentials
    with click.Context(cmd) as ctx:
        ctx.obj = deployment
        cmd.callback(show_hints=False)

    assert run_plan_spy.call_count == 2
    backup_plan = run_plan_spy.call_args_list[0][0][0]
    assert isinstance(backup_plan[0], MaasBackupClusterdCredentialsStep)
    plan = run_plan_spy.call_args_list[1][0][0]
    assert isinstance(plan[0], MaasSaveClusterdCredentialsStep)


def test_update_clusterd_credentials_reports_backup_when_update_fails(mocker):
    """Test that the backup path is reported even if the update fails."""
    deployment = Mock()
    console_spy = mocker.patch.object(provider_commands, "console")
    mocker.patch.object(provider_commands, "run_preflight_checks")
    mocker.patch.object(
        provider_commands,
        "run_plan",
        side_effect=[
            {
                "MaasBackupClusterdCredentialsStep": Result(
                    ResultType.COMPLETED, "/backup/deployments.yaml.bk.20261028170000"
                )
            },
            click.ClickException("update failed"),
        ],
    )

    cmd = provider_commands.update_clusterd_credentials
    with click.Context(cmd) as ctx:
        ctx.obj = deployment
        with pytest.raises(click.ClickException):
            cmd.callback(show_hints=False)

    console_spy.print.assert_called_once_with(
        "Previous clusterd credentials backed up to "
        "/backup/deployments.yaml.bk.20261028170000"
    )
