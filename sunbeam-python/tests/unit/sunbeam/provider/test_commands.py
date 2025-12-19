# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import click

from sunbeam.provider import commands as provider_commands
from sunbeam.provider.maas.steps import MaasSaveClusterdCredentialsStep


def test_update_clusterd_credentials_invokes_plan(tmp_path, mocker):
    """Test that the command invokes the plan with expected args."""
    deployment = Mock()
    deployment.juju_controller = Mock()
    run_plan_spy = mocker.patch.object(provider_commands, "run_plan")

    # Act
    cmd = provider_commands.update_clusterd_credentials
    with click.Context(cmd) as ctx:
        ctx.obj = deployment
        cmd.callback(show_hints=False)

    assert run_plan_spy.call_count == 1
    plan = run_plan_spy.call_args[0][0]
    assert isinstance(plan[0], MaasSaveClusterdCredentialsStep)
