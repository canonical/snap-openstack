# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from sunbeam.provider.maas.bgp import (
    EnableNeutronBgpStep,
    MicroOvnBgpAttachStep,
    MicroOvnBgpDisableStep,
    SetNodeRackNamesStep,
    ValidateNeutronBgpPluginStep,
)
from sunbeam.provider.maas.commands import configure_bgp


@pytest.fixture
def deployment():
    return MagicMock()


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, deployment, args, input=None):
    return runner.invoke(configure_bgp, args, obj=deployment, input=input)


class TestConfigureBgpArgumentValidation:
    def test_no_enable_or_disable(self, runner, deployment):
        result = invoke(runner, deployment, ["--rack", "name=rack1,peer=fe80::1"])
        assert "Specify --enable or --disable" in result.output

    def test_enable_and_disable(self, runner, deployment):
        result = invoke(
            runner,
            deployment,
            ["--enable", "--disable", "--rack", "name=rack1,peer=fe80::1"],
        )
        assert "mutually exclusive" in result.output

    def test_disable_rejects_rack_args(self, runner, deployment):
        result = invoke(
            runner, deployment, ["--disable", "--rack", "name=r1,peer=65101@fe80::1"]
        )
        assert "--disable takes no --rack arguments" in result.output

    def test_disable_runs_teardown_plan(self, runner, deployment, mocker):
        deployment.get_client.return_value = MagicMock()
        run_plan = mocker.patch("sunbeam.provider.maas.commands.run_plan")
        mocker.patch(
            "sunbeam.provider.maas.commands.run_preflight_checks",
        )
        result = invoke(runner, deployment, ["--disable"])
        assert result.exit_code == 0, result.output
        assert "BGP disabled" in result.output
        assert run_plan.call_count == 1
        plan = run_plan.call_args_list[0].args[0]
        assert [type(step) for step in plan] == [MicroOvnBgpDisableStep]

    def test_no_racks(self, runner, deployment):
        result = invoke(runner, deployment, ["--enable"])
        assert "No racks given" in result.output

    def test_malformed_rack_option(self, runner, deployment):
        result = invoke(runner, deployment, ["--enable", "--rack", "rack1"])
        assert "KEY=VALUE" in result.output

    def test_invalid_peer_address(self, runner, deployment):
        result = invoke(
            runner,
            deployment,
            ["--enable", "--rack", "name=rack1,peer=65101@nope"],
        )
        assert "Invalid BGP peer address" in result.output

    @pytest.fixture
    def discovered(self, mocker):
        discovery_step = MagicMock()
        discovery_step.rack_machines = {"rack1": ["node-a"], "rack2": ["node-c"]}
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=MagicMock(),
        )
        mocker.patch(
            "sunbeam.provider.maas.commands.run_preflight_checks",
        )
        mocker.patch(
            "sunbeam.provider.maas.commands.BgpDiscoverRackMachinesStep",
            return_value=discovery_step,
        )
        mocker.patch("sunbeam.provider.maas.commands.run_plan")
        return discovery_step

    def test_topology_validated(self, runner, deployment, mocker, discovered):
        deployment.get_client.return_value = MagicMock()
        result = invoke(
            runner,
            deployment,
            [
                "--enable",
                "--rack",
                "name=rack1,peer=65101@192.0.2.17,networks=192.0.2.18/28",
                "--rack",
                "name=rack2,peer=65102@192.0.2.33,networks=192.0.2.34/28",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "BGP topology validated for 2 rack(s)" in result.output
        assert "BGP configured for 2 rack(s)" in result.output

    def test_unknown_rack_fails(self, runner, deployment, mocker, discovered):
        deployment.get_client.return_value = MagicMock()
        discovered.rack_machines = {"rack1": ["node-a"]}
        result = invoke(
            runner,
            deployment,
            [
                "--enable",
                "--rack",
                "name=rack9,peer=65101@192.0.2.17,networks=192.0.2.18/28",
            ],
        )
        assert "Rack(s) not found in MAAS" in result.output

    def test_uncovered_rack_requires_confirmation(
        self, runner, deployment, mocker, discovered
    ):
        deployment.get_client.return_value = MagicMock()
        result = invoke(
            runner,
            deployment,
            [
                "--enable",
                "--rack",
                "name=rack1,peer=65101@192.0.2.17,networks=192.0.2.18/28",
            ],
            input="n\n",
        )
        assert "No BGP peer given for rack(s): rack2" in result.output
        assert result.exit_code != 0

    def _invoke_capturing_plan(self, runner, deployment, mocker, args):
        """Invoke configure bgp and return (result, the big plan)."""
        discovery_step = MagicMock()
        discovery_step.rack_machines = {"rack1": ["node-a"]}
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=MagicMock(),
        )
        mocker.patch("sunbeam.provider.maas.commands.run_preflight_checks")
        mocker.patch(
            "sunbeam.provider.maas.commands.BgpDiscoverRackMachinesStep",
            return_value=discovery_step,
        )
        run_plan = mocker.patch("sunbeam.provider.maas.commands.run_plan")
        result = invoke(runner, deployment, args)
        assert result.exit_code == 0, result.output
        assert run_plan.call_count == 2
        return result, run_plan.call_args_list[1].args[0]

    def test_default_plan(self, runner, deployment, mocker):
        _, plan = self._invoke_capturing_plan(
            runner,
            deployment,
            mocker,
            [
                "--enable",
                "--rack",
                "name=rack1,peer=65101@192.0.2.17,networks=192.0.2.18/28",
            ],
        )
        step_types = [type(step) for step in plan]
        assert EnableNeutronBgpStep in step_types
        assert ValidateNeutronBgpPluginStep in step_types
        assert step_types.count(MicroOvnBgpAttachStep) == 1
        assert SetNodeRackNamesStep in step_types
