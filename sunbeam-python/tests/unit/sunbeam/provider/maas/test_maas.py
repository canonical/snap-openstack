# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import json
from builtins import ConnectionRefusedError
from ssl import SSLError
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner
from lightkube import ApiError
from maas.client.bones import CallError

import sunbeam.provider.maas.steps as maas_steps
from sunbeam.core.checks import DiagnosticResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.deployments import DeploymentsConfig
from sunbeam.core.juju import ControllerNotFoundException
from sunbeam.provider.maas.commands import (
    validate_deployment_cmd,
    validate_machine_cmd,
)
from sunbeam.provider.maas.deployment import (
    ROLE_NETWORK_MAPPING,
    MaasDeployment,
    RoleTags,
    StorageTags,
)
from sunbeam.provider.maas.steps import (
    ActionFailedException,
    AddMaasDeployment,
    DeploymentRolesCheck,
    IpRangesCheck,
    MaasAddMachinesToClusterdStep,
    MaasBootstrapJujuStep,
    MaasConfigDPDKStep,
    MaasConfigureMicrocephOSDStep,
    MaasCreateLoadBalancerIPPoolsStep,
    MaasDeployInfraMachinesStep,
    MaasDeployK8SApplicationStep,
    MaasDeployMachinesStep,
    MaasScaleJujuStep,
    MachineComputeNicCheck,
    MachineNetworkCheck,
    MachineRequirementsCheck,
    MachineRolesCheck,
    MachineRootDiskCheck,
    MachineStorageCheck,
    Result,
    ResultType,
    UnitNotFoundException,
    ZoneBalanceCheck,
    ZonesCheck,
)


class TestAddMaasDeployment:
    @pytest.fixture
    def add_maas_deployment(self):
        return AddMaasDeployment(
            Mock(),
            MaasDeployment(
                name="test-deployment",
                token="test_token",
                url="test_url",
            ),
        )

    def test_is_skip_with_existing_deployment(self, add_maas_deployment, step_context):
        deployments_config = DeploymentsConfig(
            active="test-deployment",
            deployments=[
                MaasDeployment(
                    name="test-deployment",
                    url="test_url2",
                    token="test_token",
                )
            ],
        )
        add_maas_deployment.deployments_config = deployments_config
        result = add_maas_deployment.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_with_no_existing_deployment(
        self, add_maas_deployment, step_context
    ):
        deployments_config = DeploymentsConfig()
        add_maas_deployment.deployments_config = deployments_config
        result = add_maas_deployment.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_successful_connection(
        self, add_maas_deployment, mocker, step_context
    ):
        mocker.patch("sunbeam.provider.maas.client.MaasClient", autospec=True)
        result = add_maas_deployment.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_connection_refused_error(
        self, add_maas_deployment, mocker, step_context
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.MaasClient",
            side_effect=ConnectionRefusedError("Connection refused"),
        )
        result = add_maas_deployment.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_with_ssl_error(self, add_maas_deployment, mocker, step_context):
        mocker.patch(
            "sunbeam.provider.maas.client.MaasClient", side_effect=SSLError("SSL error")
        )
        result = add_maas_deployment.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_with_call_error(self, add_maas_deployment, mocker, step_context):
        mocker.patch(
            "sunbeam.provider.maas.client.MaasClient",
            side_effect=CallError(
                request={"method": "GET", "uri": "http://localhost:5240/MAAS"},
                response=Mock(status=401, reason="unauthorized"),
                content=b"",
                call=None,
            ),
        )
        result = add_maas_deployment.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_with_unknown_error(self, add_maas_deployment, mocker, step_context):
        mocker.patch(
            "sunbeam.provider.maas.client.MaasClient",
            side_effect=Exception("Unknown error"),
        )
        result = add_maas_deployment.run(step_context)
        assert result.result_type == ResultType.FAILED


class TestMachineRolesCheck:
    def test_run_with_no_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": []}
        check = MachineRolesCheck(machine)
        result = check.run()
        assert result.passed == DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"

    def test_run_with_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": ["role1", "role2"]}
        check = MachineRolesCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"


class TestRoleNetworkMapping:
    def test_control_role_requires_data_network(self):
        expected = {
            Networks.DATA,
            Networks.INTERNAL,
            Networks.MANAGEMENT,
            Networks.PUBLIC,
            Networks.STORAGE,
        }
        assert set(ROLE_NETWORK_MAPPING[RoleTags.CONTROL]) == expected

    def test_network_role_does_not_require_public_or_storage(self):
        """Network role nodes (OVN gateways) only need internal, management and data."""
        mapping = set(ROLE_NETWORK_MAPPING[RoleTags.NETWORK])
        assert Networks.PUBLIC not in mapping
        assert Networks.STORAGE not in mapping
        assert mapping == {Networks.INTERNAL, Networks.MANAGEMENT, Networks.DATA}


class TestMachineNetworkCheck:
    def test_run_with_incomplete_network_mapping(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.get_network_mapping", return_value={}
        )
        machine = {
            "hostname": "test_machine",
            "roles": ["role1", "role2"],
            "spaces": [],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "network mapping" in result.message

    def test_run_with_no_assigned_roles(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.get_network_mapping",
            return_value=dict.fromkeys(Networks.values(), "alpha"),
        )
        machine = {"hostname": "test_machine", "roles": [], "spaces": []}
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "no role assigned" in result.message

    def test_run_with_missing_spaces(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.get_network_mapping",
            return_value={
                **{network.value: "alpha" for network in Networks},
                **{Networks.PUBLIC.value: "beta"},
            },
        )
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.CONTROL.value],
            "spaces": ["alpha"],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "missing beta" in result.message

    def test_run_with_successful_check(self, mocker):
        snap = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.get_network_mapping",
            return_value={network.value: "alpha" for network in Networks},
        )
        machine = {
            "hostname": "test_machine",
            "roles": RoleTags.values(),
            "spaces": ["alpha"],
        }
        check = MachineNetworkCheck(snap, machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"


class TestMachineStorageCheck:
    def test_run_with_no_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": [], "storage": {}}
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine has no role assigned" in result.message


class TestValidateDeploymentCommand:
    def test_exit_nonzero_on_failed_checks(self, mocker):
        runner = CliRunner()
        deployment = MaasDeployment(name="test", token="token", url="url")

        mocker.patch("sunbeam.provider.maas.commands.run_preflight_checks")
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=Mock(),
        )
        mocker.patch("sunbeam.provider.maas.commands.list_machines", return_value=[])
        mocker.patch(
            "sunbeam.provider.maas.commands._run_maas_meta_checks",
            return_value=[{"passed": DiagnosticResultType.FAILURE.value}],
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._save_report",
            return_value="/tmp/report.yaml",
        )

        result = runner.invoke(validate_deployment_cmd, obj=deployment)

        assert result.exit_code == 1
        assert "Validation failed" in result.output

    def test_exit_zero_on_successful_checks(self, mocker):
        runner = CliRunner()
        deployment = MaasDeployment(name="test", token="token", url="url")

        mocker.patch("sunbeam.provider.maas.commands.run_preflight_checks")
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=Mock(),
        )
        mocker.patch("sunbeam.provider.maas.commands.list_machines", return_value=[])
        mocker.patch(
            "sunbeam.provider.maas.commands._run_maas_meta_checks",
            return_value=[{"passed": DiagnosticResultType.SUCCESS.value}],
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._save_report",
            return_value="/tmp/report.yaml",
        )

        result = runner.invoke(validate_deployment_cmd, obj=deployment)

        assert result.exit_code == 0
        assert "Validation failed" not in result.output


class TestValidateMachineCommand:
    def test_exit_nonzero_on_failed_checks(self, mocker):
        runner = CliRunner()
        deployment = MaasDeployment(name="test", token="token", url="url")

        mocker.patch("sunbeam.provider.maas.commands.run_preflight_checks")
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=Mock(),
        )
        mocker.patch(
            "sunbeam.provider.maas.commands.get_machine",
            return_value={"hostname": "node1"},
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._run_maas_checks",
            return_value=[{"passed": DiagnosticResultType.FAILURE.value}],
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._save_report",
            return_value="/tmp/report.yaml",
        )

        result = runner.invoke(validate_machine_cmd, ["node1"], obj=deployment)

        assert result.exit_code == 1
        assert "Validation failed" in result.output

    def test_exit_zero_on_successful_checks(self, mocker):
        runner = CliRunner()
        deployment = MaasDeployment(name="test", token="token", url="url")

        mocker.patch("sunbeam.provider.maas.commands.run_preflight_checks")
        mocker.patch(
            "sunbeam.provider.maas.commands.MaasClient.from_deployment",
            return_value=Mock(),
        )
        mocker.patch(
            "sunbeam.provider.maas.commands.get_machine",
            return_value={"hostname": "node1"},
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._run_maas_checks",
            return_value=[
                {"passed": DiagnosticResultType.SUCCESS.value},
                {"passed": DiagnosticResultType.WARNING.value},
            ],
        )
        mocker.patch(
            "sunbeam.provider.maas.commands._save_report",
            return_value="/tmp/report.yaml",
        )

        result = runner.invoke(validate_machine_cmd, ["node1"], obj=deployment)

        assert result.exit_code == 0

    def test_run_with_not_storage_node(self):
        machine = {
            "hostname": "test_machine",
            "roles": ["role1", "role2"],
            "storage": {},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message == "not a storage node."

    def test_run_with_no_ceph_storage(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.STORAGE.value],
            "storage": {},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "storage node has no ceph storage" in result.message
        assert result.diagnostics
        assert "https://maas.io/docs/how-to-use-storage-tags" in result.diagnostics

    def test_run_with_ceph_storage(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.STORAGE.value],
            "storage": {StorageTags.CEPH.value: ["/disk_a"]},
        }
        check = MachineStorageCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and StorageTags.CEPH.value in result.message


class TestMachineComputeNicCheck:
    def test_run_with_no_assigned_roles(self):
        machine = {"hostname": "test_machine", "roles": [], "nics": []}
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine has no role assigned" in result.message

    def test_run_with_not_compute_or_network_node(self):
        machine = {
            "hostname": "test_machine",
            "roles": ["role1", "role2"],
            "nics": [],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and "not required" in result.message

    def test_run_with_no_neutron_nic(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "no neutron NIC found" in result.message
        assert result.diagnostics
        assert "https://maas.io/docs/how-to-use-network-tags" in result.diagnostics

    def test_run_with_neutron_nic(self):
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and "neutron NIC found" in result.message

    def test_run_with_different_physnet_tag(self):
        """Any neutron:<physnet> tag should be accepted."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet2"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS


class TestMachineRootDiskCheck:
    def test_run_with_no_root_disk(self):
        machine = {"hostname": "test_machine"}
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "could not determine" in result.message

    def test_run_with_no_physical_blockdevices(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {"physical_blockdevices": []},
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "could not determine" in result.message

    def test_run_with_virtual_devices_but_no_physical_devices(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [],
                "virtual_blockdevice": {"name": "bcache01", "size": 500 * 1024**3},
                "root_partition": {"size": 500 * 1024**3},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "could not determine" in result.message

    def test_run_with_virtual_devices_and_physical_devices_but_not_all_ssds(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [
                    {"name": "nvme01", "size": 1024**4, "tags": ["ssd"]},
                    {"name": "rotary-01", "size": 1024**4, "tags": ["rotary"]},
                ],
                "virtual_blockdevice": {"name": "vg0-lv0", "size": 2 * 1024**4},
                "root_partition": {"size": 500 * 1024**3},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "is not a SSD" in result.message

    def test_run_with_virtual_devices_and_physical_devices(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [
                    {"name": "nvme01", "size": 1024**4, "tags": ["ssd"]},
                    {"name": "nvme02", "size": 1024**4, "tags": ["ssd"]},
                ],
                "virtual_blockdevice": {"name": "vg0-lv0", "size": 2 * 1024**4},
                "root_partition": {"size": 500 * 1024**3},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and "is a SSD and is large enough" in result.message

    def test_run_with_no_ssd_tag(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [
                    {"name": "rotary-01", "size": 1024**4, "tags": ["rotary"]}
                ],
                "root_partition": {"size": 500 * 1024**3},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "is not a SSD" in result.message

    def test_run_with_not_enough_space(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [
                    {"name": "nvme01", "size": 1024**4, "tags": ["ssd"]}
                ],
                "root_partition": {"size": 1},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "is too small" in result.message

    def test_run_with_valid_root_disk(self):
        machine = {
            "hostname": "test_machine",
            "root_disk": {
                "physical_blockdevices": [
                    {"name": "nvme01", "size": 1024**4, "tags": ["ssd"]}
                ],
                "root_partition": {"size": 500 * 1024**3},
            },
        }
        check = MachineRootDiskCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and "is a SSD and is large enough" in result.message


class TestMachineRequirementsCheck:
    def test_run_with_insufficient_memory(self):
        machine = {
            "hostname": "test_machine",
            "cores": 16,
            "memory": 16384,  # 16GiB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine does not meet requirements" in result.message

    def test_run_with_insufficient_cores(self):
        machine = {
            "hostname": "test_machine",
            "cores": 8,
            "memory": 32768,  # 32GiB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.details["machine"] == "test_machine"
        assert result.message and "machine does not meet requirements" in result.message

    def test_run_with_sufficient_resources(self):
        machine = {
            "hostname": "test_machine",
            "cores": 16,
            "memory": 32768,  # 32GB
            "roles": [RoleTags.CONTROL.value],
        }
        check = MachineRequirementsCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"


class TestDeploymentRolesCheck:
    def test_run_with_insufficient_roles(self):
        machines = [
            {"hostname": "machine1", "roles": ["role1", "role2"]},
            {"hostname": "machine2", "roles": ["role1"]},
            {"hostname": "machine3", "roles": ["role2"]},
        ]
        check = DeploymentRolesCheck(machines, "Role", "role1", min_count=3)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.message and "less than 3 Role" in result.message

    def test_run_with_sufficient_roles(self):
        machines = [
            {"hostname": "machine1", "roles": ["role1", "role2"]},
            {"hostname": "machine2", "roles": ["role1"]},
            {"hostname": "machine3", "roles": ["role1"]},
        ]
        check = DeploymentRolesCheck(machines, "Role", "role1", min_count=3)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.message == "Role: 3"


class TestZonesCheck:
    def test_run_with_one_zone(self):
        zones = ["zone1"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.message == "1 zone(s)"

    def test_run_with_two_zones(self):
        zones = ["zone1", "zone2"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.message == "deployment has 2 zones"

    def test_run_with_three_zones(self):
        zones = ["zone1", "zone2", "zone3"]
        check = ZonesCheck(zones)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.message == "3 zone(s)"


class TestZoneBalanceCheck:
    def test_run_with_balanced_roles(self):
        machines = {
            "zone1": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
            "zone2": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
        }
        check = ZoneBalanceCheck(machines)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.message == "deployment is balanced"

    def test_run_with_unbalanced_roles(self):
        machines = {
            "zone1": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value, RoleTags.COMPUTE.value]},
            ],
            "zone2": [
                {"roles": [RoleTags.CONTROL.value, RoleTags.STORAGE.value]},
                {"roles": [RoleTags.CONTROL.value]},
            ],
        }
        check = ZoneBalanceCheck(machines)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.message and "compute distribution is unbalanced" in result.message


class TestIpRangesCheck:
    def test_run_with_missing_network_mapping(self, mocker):
        client = Mock()
        deployment = Mock()
        deployment.network_mapping = {}
        check = IpRangesCheck(client, deployment)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.diagnostics and "network mapping" in result.diagnostics

    def test_run_with_missing_public_ip_ranges(self, mocker):
        client = Mock()
        deployment = Mock()
        deployment.network_mapping = {
            **{
                network.value: "data"
                for network in Networks
                if network != Networks.PUBLIC
            },
            **{Networks.PUBLIC.value: "public_space"},
        }
        deployment.public_api_label = "public_api"
        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space", return_value={}
        )
        check = IpRangesCheck(client, deployment)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.diagnostics and deployment.public_api_label in result.diagnostics
        get_ip_ranges_from_space_mock.assert_any_call(client, "public_space")

    def test_run_with_missing_internal_ip_ranges(self, mocker):
        client = Mock()
        deployment = Mock()
        deployment.network_mapping = {
            **{
                network.value: "data"
                for network in Networks
                if network != Networks.INTERNAL
            },
            **{Networks.INTERNAL.value: "internal_space"},
        }
        deployment.public_api_label = "public_api"
        deployment.internal_api_label = "internal_api"

        public_ip_ranges = {
            "any_cidr": [
                {
                    "start": "192.168.0.1",
                    "end": "192.168.0.10",
                    "label": "public_api",
                },
            ]
        }

        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=[public_ip_ranges, {}],
        )
        check = IpRangesCheck(client, deployment)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert (
            result.diagnostics and deployment.internal_api_label in result.diagnostics
        )
        get_ip_ranges_from_space_mock.assert_any_call(client, "internal_space")

    def test_run_with_successful_check(self, mocker):
        client = Mock()
        deployment = Mock()
        deployment.network_mapping = {
            Networks.PUBLIC.value: "public_space",
            Networks.INTERNAL.value: "internal_space",
            **{
                network.value: "data"
                for network in Networks
                if network not in (Networks.PUBLIC, Networks.INTERNAL)
            },
        }
        deployment.public_api_label = "public_api"
        deployment.internal_api_label = "internal_api"

        public_ip_ranges = {
            "192.168.0.0/24": [
                {
                    "start": "192.168.0.1",
                    "end": "192.168.0.10",
                    "label": "public_api",
                }
            ]
        }
        internal_ip_ranges = {
            "10.0.0.0/24": [
                {
                    "start": "10.0.0.1",
                    "end": "10.0.0.10",
                    "label": "internal_api",
                }
            ]
        }

        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=[public_ip_ranges, internal_ip_ranges],
        )
        check = IpRangesCheck(client, deployment)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        get_ip_ranges_from_space_mock.assert_any_call(client, "public_space")
        get_ip_ranges_from_space_mock.assert_any_call(client, "internal_space")


class TestMaasBootstrapJujuStep:
    def test_is_skip_with_no_machines(self, snap, mocker, step_context):
        maas_client = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[],
        )
        mocker.patch.object(maas_steps, "Snap", return_value=snap)
        step = MaasBootstrapJujuStep(
            maas_client=maas_client,
            cloud="test_cloud",
            cloud_type="test_cloud_type",
            controller="test_controller",
            password="test_password",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message and "No machines with tag" in result.message

    def test_is_skip_with_multiple_machines(self, snap, mocker, step_context):
        maas_client = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {"hostname": "machine1", "system_id": "1st"},
                {"hostname": "machine2", "system_id": "2nd"},
            ],
        )
        mocker.patch(
            "sunbeam.steps.juju.BootstrapJujuStep.is_skip",
            return_value=Result(ResultType.COMPLETED),
        )
        mocker.patch.object(maas_steps, "Snap", return_value=snap)
        step = MaasBootstrapJujuStep(
            maas_client=maas_client,
            cloud="test_cloud",
            cloud_type="test_cloud_type",
            controller="test_controller",
            password="test_password",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert "--to" in step.bootstrap_args
        assert step.bootstrap_args[-1].endswith("1st")

    def test_is_skip_with_single_machine(self, snap, mocker, step_context):
        maas_client = Mock()
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {"hostname": "machine1", "system_id": "1st"},
            ],
        )
        mocker.patch(
            "sunbeam.steps.juju.BootstrapJujuStep.is_skip",
            return_value=Result(ResultType.COMPLETED),
        )
        mocker.patch.object(maas_steps, "Snap", return_value=snap)
        step = MaasBootstrapJujuStep(
            maas_client=maas_client,
            cloud="test_cloud",
            cloud_type="test_cloud_type",
            controller="test_controller",
            password="test_password",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert "--to" in step.bootstrap_args
        assert step.bootstrap_args[-1].endswith("1st")


class TestMaasScaleJujuStep:
    def test_is_skip_with_controller_not_found(self, mocker, step_context):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        mocker.patch.object(
            step,
            "get_controller",
            side_effect=ControllerNotFoundException("Controller not found"),
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Controller {controller} not found"

    def test_is_skip_with_no_registered_machines(self, mocker, step_context):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": None}
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Controller {controller} has no machines registered."

    def _controller_machines_raw(self) -> list[dict]:
        return [
            {
                "hostname": "c-1",
                "blockdevice_set": [],
                "interface_set": [],
                "zone": {"name": "default"},
                "tag_names": [RoleTags.JUJU_CONTROLLER.value],
                "status_name": "deployed",
                "cpu_count": 4,
                "memory": 32768,
            },
            {
                "hostname": "c-1",
                "blockdevice_set": [],
                "interface_set": [],
                "zone": {"name": "default"},
                "tag_names": [RoleTags.JUJU_CONTROLLER.value],
                "status_name": "deployed",
                "cpu_count": 4,
                "memory": 32768,
            },
        ]

    def test_is_skip_with_already_correct_number_of_controllers(
        self, mocker, step_context
    ):
        maas_client = mocker.Mock(
            list_machines=Mock(return_value=self._controller_machines_raw())
        )
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 2
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_cannot_scale_down_controllers(self, mocker, step_context):
        maas_client = mocker.Mock(
            list_machines=Mock(return_value=self._controller_machines_raw())
        )
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 1
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == f"Can't scale down controllers from 2 to {step.n}."

    def test_is_skip_with_insufficient_juju_controllers(self, mocker, step_context):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 3
        mocker.patch.object(
            step, "get_controller", return_value={"controller-machines": [1, 2]}
        )
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            side_effect=[
                [
                    {"hostname": "machine1", "system_id": "1st"},
                    {"hostname": "machine2", "system_id": "2nd"},
                ],
                [],
            ],
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_completed(self, mocker, step_context):
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 3
        mocker.patch.object(
            step,
            "get_controller",
            return_value={"controller-machines": {"1": {"instance-id": "1st"}}},
        )
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {"hostname": "machine1", "system_id": "1st"},
                {"hostname": "machine2", "system_id": "2nd"},
                {"hostname": "machine3", "system_id": "3rd"},
            ],
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_with_no_region_controller_machines(self, mocker, step_context):
        """Test that the step continues when no region controller machines are found."""
        maas_client = mocker.Mock()
        controller = "test_controller"
        step = MaasScaleJujuStep(maas_client, controller)
        step.n = 3
        mocker.patch.object(
            step,
            "get_controller",
            return_value={"controller-machines": {"1": {"instance-id": "1st"}}},
        )
        # First call returns juju-controller machines, second call raises ValueError
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            side_effect=[
                [
                    {"hostname": "machine1", "system_id": "1st"},
                    {"hostname": "machine2", "system_id": "2nd"},
                    {"hostname": "machine3", "system_id": "3rd"},
                ],
                ValueError(
                    "No machines found"
                ),  # Simulates no region controller machines
            ],
        )
        result = step.is_skip(step_context)
        # Should complete successfully even without region controller machines
        assert result.result_type == ResultType.COMPLETED
        assert "--to" in " ".join(step.extra_args)


class TestMaasAddMachinesToClusterdStep:
    @pytest.fixture
    def maas_add_machines_to_clusterd_step(self):
        client = Mock()
        maas_client = Mock()
        return MaasAddMachinesToClusterdStep(client, maas_client)

    def test_is_skip_with_no_filtered_machines(
        self,
        mocker,
        maas_add_machines_to_clusterd_step,
        step_context,
    ):
        mocker.patch("sunbeam.provider.maas.client.list_machines", return_value=[])
        result = maas_add_machines_to_clusterd_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Maas deployment has no machines."

    def test_is_skip_with_filtered_machines(
        self,
        mocker,
        maas_add_machines_to_clusterd_step,
        step_context,
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {"hostname": "machine1", "roles": [RoleTags.CONTROL.value]},
                {"hostname": "machine2", "roles": [RoleTags.COMPUTE.value]},
            ],
        )
        maas_add_machines_to_clusterd_step.client.cluster.list_nodes.return_value = []
        result = maas_add_machines_to_clusterd_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_with_no_machines_and_nodes(
        self, maas_add_machines_to_clusterd_step, step_context
    ):
        maas_add_machines_to_clusterd_step.machines = None
        maas_add_machines_to_clusterd_step.nodes = None
        result = maas_add_machines_to_clusterd_step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "No machines to add / node to update."

    def test_run_with_machines_and_nodes(
        self, maas_add_machines_to_clusterd_step, step_context
    ):
        maas_add_machines_to_clusterd_step.machines = [
            {
                "hostname": "machine1",
                "roles": [RoleTags.CONTROL.value],
                "system_id": "1st",
            },
            {
                "hostname": "machine2",
                "roles": [RoleTags.COMPUTE.value],
                "system_id": "2nd",
            },
        ]
        maas_add_machines_to_clusterd_step.nodes = [
            ("machine1", [RoleTags.CONTROL.value]),
            ("machine2", [RoleTags.COMPUTE.value]),
        ]
        result = maas_add_machines_to_clusterd_step.run(step_context)
        assert result.result_type == ResultType.COMPLETED


class TestMaasDeployMachinesStep:
    @pytest.fixture
    def maas_deploy_machines_step(self):
        deployment = Mock()
        deployment.resource_tag = "test-tag"
        client = Mock()
        jhelper = Mock()
        model = "test_model"
        return MaasDeployMachinesStep(deployment, client, jhelper, model)

    def test_is_skip_with_no_clusterd_nodes(
        self, maas_deploy_machines_step, step_context
    ):
        maas_deploy_machines_step.client.cluster.list_nodes.return_value = []
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "No machines found in clusterd."

    def test_is_skip_with_juju_controller_nodes(
        self, maas_deploy_machines_step, step_context
    ):
        maas_deploy_machines_step.client.cluster.list_nodes.return_value = [
            {"name": "test_node", "machineid": 1, "role": ["juju-controller"]}
        ]
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_infra_nodes(self, maas_deploy_machines_step, step_context):
        maas_deploy_machines_step.client.cluster.list_nodes.return_value = [
            {"name": "test_node", "machineid": 1, "role": ["sunbeam"]}
        ]
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_existing_machine_id(
        self, maas_deploy_machines_step, step_context
    ):
        maas_deploy_machines_step.client.cluster.list_nodes.return_value = [
            {"name": "test_node", "machineid": 1}
        ]
        maas_deploy_machines_step.jhelper.get_machines.return_value = {
            "2": Mock(hostname="test_node")
        }
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        msg = (
            "Machine test_node already exists in model test_model with id 2,"
            " expected the id 1."
        )
        assert result.message == msg

    def test_is_skip_with_nodes_to_deploy(
        self, maas_deploy_machines_step, step_context
    ):
        maas_deploy_machines_step.client.cluster.list_nodes.return_value = [
            {"name": "test_node", "machineid": -1}
        ]
        maas_deploy_machines_step.jhelper.get_machines.return_value = {}
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run(self, maas_deploy_machines_step, step_context):
        maas_deploy_machines_step.nodes_to_deploy = [
            {"name": "test_node1", "systemid": "1st"},
            {"name": "test_node2", "systemid": "2nd"},
        ]
        maas_deploy_machines_step.nodes_to_update = [
            {"name": "test_node3"},
            {"name": "test_node4"},
        ]
        maas_deploy_machines_step.jhelper.get_machines.return_value = {
            "1": Mock(hostname="test_node3", id=1),
            "2": Mock(hostname="test_node4", id=2),
        }

        maas_deploy_machines_step.jhelper.add_machine.side_effect = ["0", "1"]
        result = maas_deploy_machines_step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert maas_deploy_machines_step.client.cluster.update_node_info.call_count == 4
        assert (
            maas_deploy_machines_step.jhelper.wait_all_machines_deployed.call_count == 1
        )


class TestMaasDeployInfraMachinesStep:
    @pytest.fixture
    def maas_deploy_machines_step(self):
        maas_client = Mock()
        deployment = Mock()
        deployment.resource_tag = "test-tag"
        jhelper = Mock()
        model = "test_model"
        return MaasDeployInfraMachinesStep(maas_client, deployment, jhelper, model)

    def test_is_skip(self, mocker, maas_deploy_machines_step, step_context):
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {
                    "hostname": "test_node1",
                    "system_id": "1st",
                    "roles": [RoleTags.SUNBEAM.value],
                }
            ],
        )
        maas_deploy_machines_step.jhelper.get_machines.return_value = {}
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_no_infra_nodes(
        self, mocker, maas_deploy_machines_step, step_context
    ):
        mocker.patch("sunbeam.provider.maas.client.list_machines", return_value=[])
        maas_deploy_machines_step.jhelper.get_machines.return_value = {}
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_all_infra_nodes_deployed(
        self, mocker, maas_deploy_machines_step, step_context
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.list_machines",
            return_value=[
                {
                    "hostname": "test_node1",
                    "system_id": "1st",
                    "roles": [RoleTags.SUNBEAM.value],
                }
            ],
        )
        maas_deploy_machines_step.jhelper.get_machines.return_value = {
            "1": Mock(hostname="test_node1")
        }
        result = maas_deploy_machines_step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, mocker, maas_deploy_machines_step, step_context):
        maas_deploy_machines_step.machines_to_deploy = [
            {
                "hostname": "test_node1",
                "system_id": "1st",
                "roles": [RoleTags.SUNBEAM.value],
            },
            {
                "hostname": "test_node2",
                "system_id": "2nd",
                "roles": [RoleTags.SUNBEAM.value],
            },
        ]
        result = maas_deploy_machines_step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert maas_deploy_machines_step.jhelper.add_machine.call_count == 2
        assert (
            maas_deploy_machines_step.jhelper.wait_all_machines_deployed.call_count == 1
        )


class TestMaasConfigureMicrocephOSDStep:
    @pytest.fixture
    def jhelper(self):
        jhelper = Mock()
        jhelper.get_leader_unit = Mock(return_value="leader_unit")
        jhelper.get_unit_from_machine = Mock(return_value="unit/1")
        jhelper.get_model = Mock()
        jhelper.get_model_closing = Mock()
        return jhelper

    @pytest.fixture
    def step(self, jhelper):
        client = Mock()
        maas_client = Mock()
        manifest = Mock()
        names = ["machine1", "machine2"]
        step = MaasConfigureMicrocephOSDStep(
            client, maas_client, jhelper, names, manifest, "test-model"
        )
        return step

    @pytest.fixture
    def microceph_disks(self):
        return {
            "machine1": {
                "osds": ["/dev/sdb", "/dev/sdc"],
                "unpartitioned_disks": ["/dev/sdd"],
                "unit": "unit/1",
            },
            "machine2": {
                "osds": ["/dev/sde"],
                "unpartitioned_disks": ["/dev/sdf", "/dev/sdg"],
                "unit": "unit/2",
            },
        }

    @pytest.fixture
    def maas_disks(self):
        return {
            "machine1": ["/dev/sdb", "/dev/sdc"],
            "machine2": ["/dev/sde", "/dev/sdf"],
        }

    @pytest.fixture
    def step_with_disks(self, step, microceph_disks, maas_disks):
        step._get_microceph_disks = Mock(return_value=microceph_disks)
        step._get_maas_disks = Mock(return_value=maas_disks)
        return step

    def test_get_microceph_disks(self, step, jhelper, microceph_disks):
        osds = (
            '[{"location": "machine1", "path": "/dev/sdb"},'
            ' {"location": "machine1", "path": "/dev/sdc"},'
            ' {"location": "machine2", "path": "/dev/sde"}]'
        )
        jhelper.run_action = Mock(
            side_effect=[
                {
                    "osds": (osds),
                    "unpartitioned-disks": '[{"path": "/dev/sdd"}]',
                },
                {
                    "osds": (osds),
                    "unpartitioned-disks": '[{"path": "/dev/sdf"},'
                    ' {"path": "/dev/sdg"}]',
                },
            ]
        )
        step.client.cluster.get_node_info.return_value = {"machineid": 1}
        step.client.cluster.list_nodes.return_value = [
            {"name": "machine1"},
            {"name": "machine2"},
        ]
        step.jhelper.get_unit_from_machine.side_effect = [
            "unit/1",
            "unit/2",
        ]
        step.jhelper.get_machines.return_value = {
            "machine1": Mock(hostname="test_node1"),
            "machine2": Mock(hostname="test_node2"),
        }

        ctxt_mgr = Mock()
        ctxt_mgr.return_value = Mock(
            machines={
                "1": Mock(hostname="test_node1", id=1),
                "2": Mock(hostname="test_node2", id=2),
            }
        )
        ctxt_mgr._aexit__.return_value = None
        step.jhelper.get_model_closing = Mock(return_value=ctxt_mgr)

        # Call the method under test
        result = step._get_microceph_disks()

        expected_osds = [osd["path"] for osd in json.loads(osds)]
        expected_microceph_disks = copy.deepcopy(microceph_disks)
        for unit, disks in expected_microceph_disks.items():
            expected_microceph_disks[unit]["osds"] = expected_osds

        # Assert the result
        assert result == expected_microceph_disks

    def test_list_disks(self, step, jhelper):
        jhelper.run_action = Mock(
            return_value={
                "osds": (
                    '[{"location": "machine1", "path": "/dev/sdb"},'
                    ' {"location": "machine1", "path": "/dev/sdc"}]'
                ),
                "unpartitioned-disks": '[{"path": "/dev/sdd"}]',
            }
        )
        result = step._list_disks("unit1")
        assert result == (
            [
                {"location": "machine1", "path": "/dev/sdb"},
                {"location": "machine1", "path": "/dev/sdc"},
            ],
            [{"path": "/dev/sdd"}],
        )

    def test_compute_disks_to_configure(self, step):
        microceph_disks = {
            "osds": ["/dev/sdb", "/dev/sdc"],
            "unpartitioned_disks": ["/dev/sdd", "/dev/sde"],
            "unit": "unit/1",
        }
        maas_disks = {"/dev/sdb", "/dev/sdc", "/dev/sdd"}
        result = step._compute_disks_to_configure(microceph_disks, maas_disks)
        assert result == ["/dev/sdd"]

    def test_compute_disks_to_configure_no_maas_disks(self, step):
        microceph_disks = {
            "osds": ["/dev/sdb", "/dev/sdc"],
            "unpartitioned_disks": ["/dev/sdd"],
            "unit": "unit/1",
        }
        maas_disks = set()
        with pytest.raises(ValueError) as e:
            step._compute_disks_to_configure(microceph_disks, maas_disks)
        assert str(e.value) == "Machine 'unit/1' does not have any 'ceph' disk defined."

    def test_compute_disks_to_configure_unknown_osds(self, step):
        microceph_disks = {
            "osds": ["/dev/sdb", "/dev/sdc", "/dev/sdd"],
            "unpartitioned_disks": ["/dev/sde"],
            "unit": "unit/1",
        }
        maas_disks = {"/dev/sdb", "/dev/sdc"}
        with pytest.raises(ValueError) as e:
            step._compute_disks_to_configure(microceph_disks, maas_disks)
        exc_msg = "Machine 'unit/1' has OSDs from disks unknown to MAAS: {'/dev/sdd'}"
        assert str(e.value) == exc_msg

    def test_compute_disks_to_configure_missing_disks(self, step):
        microceph_disks = {
            "osds": ["/dev/sdb", "/dev/sdc"],
            "unpartitioned_disks": ["/dev/sdd"],
            "unit": "unit1",
        }
        maas_disks = {"/dev/sdb", "/dev/sdc", "/dev/sde"}
        with pytest.raises(ValueError) as e:
            step._compute_disks_to_configure(microceph_disks, maas_disks)
        assert str(e.value) == "Machine 'unit1' is missing disks: {'/dev/sde'}"

    def test_is_skip_completed(self, step_with_disks, step_context):
        result = step_with_disks.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_failed_get_microceph_disks(self, step, step_context):
        step._get_microceph_disks = Mock(
            side_effect=ValueError("Failed to list microceph disks from units")
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Failed to list microceph disks from units"

    def test_is_skip_failed_get_maas_disks(self, step, step_context):
        step._get_microceph_disks = Mock(return_value={})
        step._get_maas_disks = MagicMock(
            side_effect=ValueError("Failed to list disks from MAAS")
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Failed to list disks from MAAS"

    def test_run(self, step_with_disks, jhelper, step_context):
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step_with_disks.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_failed_run_action(self, step_with_disks, jhelper, step_context):
        step_with_disks.disks_to_configure = {"unit/1": ["/dev/sdd"]}
        step_with_disks.unit_to_hostname = {"unit/1": "machine1"}
        jhelper.run_action = Mock(
            side_effect=ActionFailedException("Failed to run action")
        )
        result = step_with_disks.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Failed to run action"

    def test_run_failed_unit_not_found(self, step_with_disks, jhelper, step_context):
        step_with_disks.disks_to_configure = {"unit/1": ["/dev/sdd"]}
        step_with_disks.unit_to_hostname = {"unit/1": "machine1"}
        jhelper.run_action = Mock(side_effect=UnitNotFoundException("Unit not found"))
        result = step_with_disks.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit not found"

    def test_wipe_requested_true(self, step):
        step.manifest.core.config.microceph_config = Mock()
        machine_cfg = Mock()
        machine_cfg.dangerous_i_acknowledge_i_will_lose_data_wipe_disks = True
        step.manifest.core.config.microceph_config.root.get = Mock(
            return_value=machine_cfg
        )
        assert step._wipe_requested("machine1") is True

    def test_wipe_requested_false(self, step):
        step.manifest.core.config.microceph_config = Mock()
        machine_cfg = Mock()
        machine_cfg.dangerous_i_acknowledge_i_will_lose_data_wipe_disks = False
        step.manifest.core.config.microceph_config.root.get = Mock(
            return_value=machine_cfg
        )
        assert step._wipe_requested("machine1") is False

    def test_wipe_requested_no_machine_config(self, step):
        step.manifest.core.config.microceph_config = Mock()
        step.manifest.core.config.microceph_config.root.get = Mock(return_value=None)
        assert step._wipe_requested("machine1") is False

    def test_wipe_requested_no_microceph_config(self, step):
        step.manifest.core.config.microceph_config = None
        assert step._wipe_requested("machine1") is False

    def test_run_with_wipe_disks_true(self, step_with_disks, jhelper, step_context):
        step_with_disks.disks_to_configure = {"unit/1": ["/dev/sdd"]}
        step_with_disks.unit_to_hostname = {"unit/1": "machine1"}
        step_with_disks.manifest.core.config.microceph_config = Mock()
        machine_cfg = Mock()
        machine_cfg.dangerous_i_acknowledge_i_will_lose_data_wipe_disks = True
        step_with_disks.manifest.core.config.microceph_config.root.get = Mock(
            return_value=machine_cfg
        )
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step_with_disks.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "unit/1",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdd", "wipe": True},
        )

    def test_run_with_wipe_disks_false(self, step_with_disks, jhelper, step_context):
        step_with_disks.disks_to_configure = {"unit/1": ["/dev/sdd"]}
        step_with_disks.unit_to_hostname = {"unit/1": "machine1"}
        step_with_disks.manifest.core.config.microceph_config = None
        jhelper.run_action = Mock(return_value={"status": "completed"})
        result = step_with_disks.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        jhelper.run_action.assert_called_once_with(
            "unit/1",
            "test-model",
            "add-osd",
            action_params={"device-id": "/dev/sdd"},
        )


@pytest.fixture()
def deployment_k8s():
    dep = Mock()
    dep.name = "test_deployment"
    dep.public_api_label = "public_api"
    dep.internal_api_label = "internal_api"

    def get_space(network):
        if network == Networks.PUBLIC:
            return "public_space"
        elif network == Networks.INTERNAL:
            return "internal_space"
        return "data"

    dep.get_space.side_effect = get_space
    yield dep


class TestMaasDeployK8SApplicationStep:
    def test_extra_tfvars_with_ranges(self, deployment_k8s):
        step = MaasDeployK8SApplicationStep(
            deployment_k8s,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            "test-model",
        )
        step.ranges = "10.0.0.0/28"
        step.client.cluster.get_config.return_value = "{}"
        expected_tfvars = {
            "endpoint_bindings": [
                {"space": "data"},
                {"endpoint": "cluster", "space": "internal_space"},
            ],
            "k8s_config": {
                "kube-apiserver-extra-args": "default-not-ready-toleration-seconds=60 default-unreachable-toleration-seconds=60",
                "load-balancer-cidrs": "10.0.0.0/28",
                "load-balancer-enabled": True,
                "load-balancer-l2-mode": True,
                "node-labels": "sunbeam/deployment=test_deployment",
            },
        }

        assert step.extra_tfvars() == expected_tfvars

    def test_is_skip_with_public_ranges_error(
        self, mocker, deployment_k8s, step_context
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=ValueError("Failed to get ip ranges"),
        )
        step = MaasDeployK8SApplicationStep(
            deployment_k8s,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            "test-model",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Failed to find ip ranges for space: 'public_space'"

    def test_is_skip_with_no_public_ranges(self, mocker, deployment_k8s, step_context):
        mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            return_value={},
        )
        step = MaasDeployK8SApplicationStep(
            deployment_k8s,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            "test-model",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert (
            result.message == "Failed to find public ip range for label: 'public_api'"
        )

    def test_is_skip_with_internal_ranges_error(
        self, mocker, deployment_k8s, step_context
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=[
                {
                    "10.0.0.0/24": [
                        {
                            "start": "10.0.0.10",
                            "end": "10.0.0.20",
                            "label": "public_api",
                        }
                    ]
                },
                ValueError("Failed to get ip ranges"),
            ],
        )
        step = MaasDeployK8SApplicationStep(
            deployment_k8s,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            "test-model",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert result.message == "Failed to find ip ranges for space: 'internal_space'"

    def test_is_skip_with_no_internal_ranges(
        self, mocker, deployment_k8s, step_context
    ):
        mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=[
                {
                    "10.0.0.0/24": [
                        {
                            "start": "10.0.0.10",
                            "end": "10.0.0.20",
                            "label": "public_api",
                        }
                    ]
                },
                {},
            ],
        )
        step = MaasDeployK8SApplicationStep(
            deployment_k8s,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            "test-model",
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert (
            result.message == "Failed to find public ip range for label: 'internal_api'"
        )


class FakeIPPool:
    """k8s ipaddresspool."""

    def __init__(self, addresses):
        self.spec = {"addresses": addresses}


class FakeL2Advertisement:
    """k8s ipaddresspool."""

    def __init__(self, name):
        self.spec = {"ipAddressPools": [name]}


class TestMaasCreateLoadBalancerIPPoolsStep:
    @pytest.fixture
    def read_config(self, mocker):
        kubeconfig = {
            "apiVersion": "v1",
            "clusters": [
                {
                    "cluster": {
                        "server": "http://localhost:8888",
                    },
                    "name": "mock-cluster",
                }
            ],
            "contexts": [
                {
                    "context": {"cluster": "mock-cluster", "user": "admin"},
                    "name": "mock",
                }
            ],
            "current-context": "mock",
            "kind": "Config",
            "preferences": {},
            "users": [{"name": "admin", "user": {"token": "mock-token"}}],
        }

        with mocker.patch(
            "sunbeam.core.steps.read_config", return_value=kubeconfig
        ) as p:
            yield p

    def test_run(self, deployment_k8s, mocker, read_config, step_context):
        pool_name = deployment_k8s.public_api_label
        ippool_from_maas = {
            "10.149.100.128/25": [
                {"label": pool_name, "start": "10.149.100.200", "end": "10.149.100.210"}
            ]
        }

        cclient = Mock()
        maas_client = Mock()
        k8s_snap = Mock()
        mocker.patch("sunbeam.core.k8s.Snap", k8s_snap)
        mocker.patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        side_effect=[
                            FakeIPPool(["10.149.100.200-10.149.100.210"]),
                            FakeL2Advertisement(pool_name),
                            FakeIPPool(["10.149.100.200-10.149.100.210"]),
                            FakeL2Advertisement(pool_name),
                        ]
                    )
                )
            ),
        )
        k8s_snap().config.get.return_value = "k8s"
        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            return_value=ippool_from_maas,
        )

        step = MaasCreateLoadBalancerIPPoolsStep(deployment_k8s, cclient, maas_client)

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        get_ip_ranges_from_space_mock.assert_any_call(maas_client, "public_space")
        step.kube.create.assert_not_called()

    def test_run_with_missing_ipaddresspool(
        self, deployment_k8s, mocker, read_config, step_context
    ):
        pool_name = deployment_k8s.public_api_label
        ippool_from_maas = {
            "10.149.100.128/25": [
                {"label": pool_name, "start": "10.149.100.200", "end": "10.149.100.210"}
            ]
        }

        cclient = Mock()
        maas_client = Mock()
        k8s_snap = Mock()
        mocker.patch("sunbeam.core.k8s.Snap", k8s_snap)
        mocker.patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=Mock(side_effect=[None, None, None, None]))),
        )
        k8s_snap().config.get.return_value = "k8s"

        def get_ip_ranges_side_effect(client, space):
            if space == "public_space":
                return ippool_from_maas
            return {}  # No storage ranges

        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=get_ip_ranges_side_effect,
        )

        step = MaasCreateLoadBalancerIPPoolsStep(deployment_k8s, cclient, maas_client)

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        get_ip_ranges_from_space_mock.assert_any_call(maas_client, "public_space")
        # Only public pool is created when storage ranges don't exist
        assert step.kube.create.call_count == 1
        assert step.kube.create.mock_calls[0][1][0].get("spec", {}).get(
            "addresses"
        ) == ["10.149.100.200-10.149.100.210"]

    def test_run_with_missing_l2advertisement(
        self,
        deployment_k8s,
        mocker,
        read_config,
        step_context,
    ):
        pool_name = deployment_k8s.public_api_label
        ippool_from_maas = {
            "10.149.100.128/25": [
                {"label": pool_name, "start": "10.149.100.200", "end": "10.149.100.210"}
            ]
        }

        cclient = Mock()
        maas_client = Mock()
        k8s_snap = Mock()
        mocker.patch("sunbeam.core.k8s.Snap", k8s_snap)
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=404)
        mocker.patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        side_effect=[
                            FakeIPPool(["10.149.100.200-10.149.100.210"]),
                            api_error,
                            FakeIPPool(["10.149.100.200-10.149.100.210"]),
                            api_error,
                        ]
                    )
                )
            ),
        )
        k8s_snap().config.get.return_value = "k8s"

        def get_ip_ranges_side_effect(client, space):
            if space == "public_space":
                return ippool_from_maas
            return {}  # No storage ranges

        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=get_ip_ranges_side_effect,
        )

        step = MaasCreateLoadBalancerIPPoolsStep(deployment_k8s, cclient, maas_client)

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        get_ip_ranges_from_space_mock.assert_any_call(maas_client, "public_space")
        # Only 2 get calls for public pool (ipaddresspool + l2advertisement)
        assert step.kube.get.call_count == 2
        assert step.kube.delete.call_count == 0

    def test_run_with_different_ippool(
        self, deployment_k8s, mocker, read_config, step_context
    ):
        pool_name = deployment_k8s.public_api_label
        ippool_from_maas = {
            "10.149.100.128/25": [
                {"label": pool_name, "start": "10.149.100.200", "end": "10.149.100.210"}
            ]
        }

        cclient = Mock()
        maas_client = Mock()
        k8s_snap = Mock()
        mocker.patch("sunbeam.core.k8s.Snap", k8s_snap)
        mocker.patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        side_effect=[
                            FakeIPPool(["10.149.100.120-10.149.100.130"]),
                            FakeL2Advertisement(pool_name),
                            FakeIPPool(["10.149.100.120-10.149.100.130"]),
                            FakeL2Advertisement(pool_name),
                        ]
                    )
                )
            ),
        )
        k8s_snap().config.get.return_value = "k8s"

        def get_ip_ranges_side_effect(client, space):
            if space == "public_space":
                return ippool_from_maas
            return {}  # No storage ranges

        get_ip_ranges_from_space_mock = mocker.patch(
            "sunbeam.provider.maas.client.get_ip_ranges_from_space",
            side_effect=get_ip_ranges_side_effect,
        )

        step = MaasCreateLoadBalancerIPPoolsStep(deployment_k8s, cclient, maas_client)

        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED
        get_ip_ranges_from_space_mock.assert_any_call(maas_client, "public_space")
        # Only public pool is replaced when storage ranges don't exist
        assert step.kube.replace.call_count == 1
        assert step.kube.replace.mock_calls[0][1][0].spec.get("addresses") == [
            "10.149.100.200-10.149.100.210"
        ]


class TestMaasConfigDPDKStep:
    @patch("sunbeam.provider.maas.client.MaasClient")
    def _get_step(self, mock_maas_client, manifest=None):
        return MaasConfigDPDKStep(
            deployment=Mock(),
            client=Mock(),
            jhelper=Mock(),
            model="test-model",
            manifest=manifest,
            accept_defaults=True,
        )

    @patch("sunbeam.provider.maas.client.list_machines")
    def test_get_nics(self, mock_list_machines):
        mock_list_machines.return_value = [
            {
                "hostname": "node1",
                "nics": [
                    {"name": "eth1", "tags": ["some-tag", "neutron:dpdk"]},
                    {"name": "eth2", "tags": []},
                ],
            },
            {
                "hostname": "node2",
                "nics": [
                    {"name": "eth1", "tags": ["some-tag"]},
                    {"name": "eth2", "tags": ["neutron:dpdk"]},
                ],
            },
            {
                "hostname": "node3",
                "nics": [
                    {"name": "eth1", "tags": []},
                    {"name": "eth2", "tags": []},
                ],
            },
            {
                "hostname": "node4",
                "nics": [
                    {"name": "eth1", "tags": []},
                    {"name": "eth2", "tags": []},
                ],
            },
        ]

        manifest = Mock()
        manifest.core.config.dpdk.ports = {
            "node2": ["eth1"],
            "node3": ["eth1"],
        }

        expected_nics = {
            "node1": ["eth1"],
            "node2": ["eth2", "eth1"],
            "node3": ["eth1"],
            "node4": [],
        }

        step = self._get_step(manifest=manifest)
        step._prompt_nics()

        assert expected_nics == step.nics


class TestMachineComputeNicCheckNetworkNode:
    """Test MachineComputeNicCheck validates network nodes correctly."""

    def test_run_with_network_node_and_nic(self):
        """Network node with neutron tag should pass."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth1", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.details["machine"] == "test_machine"
        assert result.message and "neutron NIC found" in result.message

    def test_run_with_network_node_no_nic(self):
        """Network node without neutron tag should fail."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth1", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE
        assert result.details["machine"] == "test_machine"
        assert result.message and "no neutron NIC found" in result.message

    def test_run_with_compute_and_network_both_need_nic(self):
        """Both compute and network nodes require a neutron NIC."""
        for role in [RoleTags.COMPUTE.value, RoleTags.NETWORK.value]:
            machine = {
                "hostname": f"test_machine_{role}",
                "roles": [role],
                "nics": [{"name": "eth1", "tags": ["neutron:physnet1"]}],
            }
            check = MachineComputeNicCheck(machine)
            result = check.run()
            assert result.passed is DiagnosticResultType.SUCCESS
            assert result.message and "neutron NIC found" in result.message

    def test_run_with_network_node_different_physnet(self):
        """Network node with any neutron:<physnet> tag should pass."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth1", "tags": ["neutron:external"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS


class TestMachineComputeNicCheckSplitRoles:
    """Test MachineComputeNicCheck behavior when feature.split-roles is enabled."""

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_only_no_nic_success(self, mock_split):
        """Split-roles ON + compute-only node without NIC → SUCCESS."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS
        assert result.message and "not required" in result.message

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_only_with_nic_warns(self, mock_split):
        """Split-roles ON + compute-only with neutron NIC → WARNING."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.message and "will be ignored" in result.message

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_network_node_no_nic_failure(self, mock_split):
        """Split-roles ON + network node without NIC → FAILURE."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_network_node_with_nic_success(self, mock_split):
        """Split-roles ON + network node with NIC → SUCCESS."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_network_with_nic_success(self, mock_split):
        """Split-roles ON + compute+network node with NIC → SUCCESS."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value, RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_compute_network_no_nic_failure(self, mock_split):
        """Split-roles ON + compute+network node without NIC → FAILURE."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value, RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_control_only_success(self, mock_split):
        """Split-roles ON + control-only node → SUCCESS."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.CONTROL.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_control_with_nic_warns(self, mock_split):
        """Split-roles ON + control-only with neutron NIC → WARNING."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.CONTROL.value],
            "nics": [{"name": "eth0", "tags": ["neutron:physnet1"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING
        assert result.message and "will be ignored" in result.message

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_on_no_roles_failure(self, mock_split):
        """Split-roles ON + no roles → FAILURE."""
        machine = {
            "hostname": "test_machine",
            "roles": [],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=False)
    def test_split_roles_off_compute_only_no_nic_failure(self, mock_split):
        """Split-roles OFF + compute-only without NIC → FAILURE."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=False)
    def test_split_roles_off_network_only_no_nic_failure(self, mock_split):
        """Split-roles OFF + network-only without NIC → FAILURE."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": []}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE


class TestMachineComputeNicCheckMultiPhysnet:
    """Test NIC checks with different physnets on different hosts."""

    def test_different_physnets_on_network_nodes(self):
        """Two network nodes, each with a different physnet, both pass."""
        for physnet in ["neutron:physnet1", "neutron:physnet2"]:
            machine = {
                "hostname": "test_machine",
                "roles": [RoleTags.NETWORK.value],
                "nics": [{"name": "eth1", "tags": [physnet]}],
            }
            check = MachineComputeNicCheck(machine)
            result = check.run()
            assert result.passed is DiagnosticResultType.SUCCESS

    def test_multiple_neutron_nics_on_one_host(self):
        """A network node with two NICs for different physnets passes."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [
                {"name": "eth1", "tags": ["neutron:physnet1"]},
                {"name": "eth2", "tags": ["neutron:physnet2"]},
            ],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.SUCCESS

    def test_non_neutron_tags_ignored(self):
        """NICs with non-neutron tags don't satisfy the check."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.NETWORK.value],
            "nics": [{"name": "eth0", "tags": ["sometag", "othertag"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.FAILURE

    @patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=True)
    def test_split_roles_compute_with_physnet2_warns(self, mock_split):
        """Compute-only with neutron:physnet2 warns (NIC ignored)."""
        machine = {
            "hostname": "test_machine",
            "roles": [RoleTags.COMPUTE.value],
            "nics": [{"name": "eth1", "tags": ["neutron:physnet2"]}],
        }
        check = MachineComputeNicCheck(machine)
        result = check.run()
        assert result.passed is DiagnosticResultType.WARNING


class TestMaasDeploymentProperties:
    """Test MAAS deployment properties."""

    def test_storage_ippool_label(self):
        """Test storage_ippool_label property returns correct value."""
        deployment = MaasDeployment(
            name="test-deployment",
            url="http://maas.local",
            token="test-token",
        )
        assert deployment.storage_ippool_label == "test-deployment-storage-ippool"

    def test_storage_ip_pool(self):
        """Test storage_ip_pool property returns correct value."""
        deployment = MaasDeployment(
            name="test-deployment",
            url="http://maas.local",
            token="test-token",
        )
        assert deployment.storage_ip_pool == "test-deployment-storage-ippool"

    def test_storage_ippool_label_with_different_names(self):
        """Test storage_ippool_label with different deployment names."""
        test_cases = [
            ("my-cloud", "my-cloud-storage-ippool"),
            ("prod-deployment", "prod-deployment-storage-ippool"),
            ("dev", "dev-storage-ippool"),
        ]

        for deployment_name, expected_label in test_cases:
            deployment = MaasDeployment(
                name=deployment_name,
                url="http://maas.local",
                token="test-token",
            )
            assert deployment.storage_ippool_label == expected_label
            assert deployment.storage_ip_pool == expected_label


class TestIsMaasDeployment:
    """Test is_maas_deployment TypeGuard function."""

    def test_is_maas_deployment_with_maas_deployment(self):
        """Test that MaasDeployment returns True."""
        from sunbeam.provider.maas.deployment import is_maas_deployment

        deployment = MaasDeployment(
            name="test-deployment",
            url="http://maas.local",
            token="test-token",
        )
        assert is_maas_deployment(deployment) is True

    def test_is_maas_deployment_with_base_deployment(self, mocker):
        """Test that non-MAAS Deployment returns False."""
        from sunbeam.core.deployment import Deployment
        from sunbeam.provider.maas.deployment import is_maas_deployment

        # Create a mock non-MAAS deployment
        deployment = mocker.Mock(spec=Deployment)
        assert is_maas_deployment(deployment) is False
