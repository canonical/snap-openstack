# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock

import yaml

from sunbeam.core.common import ResultType
from sunbeam.steps.role_distributor import (
    DeployRoleDistributorApplicationStep,
    ReapplyRoleDistributorApplicationStep,
    RemoveRoleDistributorUnitsStep,
)
from sunbeam.versions import DEPLOY_ROLE_DISTRIBUTOR_TFVAR_MAP


def test_role_distributor_config_is_not_generic_manifest_tfvar():
    role_distributor_tfvars = DEPLOY_ROLE_DISTRIBUTOR_TFVAR_MAP["charms"][
        "role-distributor"
    ]

    assert "config" not in role_distributor_tfvars


class TestDeployRoleDistributorApplicationStep:
    def test_extra_tfvars_emits_role_mapping_config(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = [
            "1",
            "2",
        ]
        basic_client.cluster.list_nodes_by_role.side_effect = lambda role: {
            "control": [{"machineid": "0", "role": ["control"]}],
            "compute": [{"machineid": "1", "role": ["compute"]}],
            "network": [{"machineid": "2", "role": ["network"]}],
        }.get(role, [])

        step = DeployRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

        extra_tfvars = step.extra_tfvars()

        role_mapping = yaml.safe_load(
            extra_tfvars["charm_role_distributor_config"]["role-mapping"]
        )
        assert role_mapping["openstack-machines"]["microovn"]["machines"] == {
            "1": {"roles": ["chassis"]},
            "2": {"roles": ["chassis", "gateway"]},
        }
        assert extra_tfvars["role_distributor_machine_ids"] == ["0"]

    def test_get_accepted_application_status_allows_waiting(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        step = DeployRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

        assert step.get_accepted_application_status() == ["active", "waiting"]

    def test_extra_tfvars_uses_manifest_config_except_role_mapping(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = []
        basic_client.cluster.list_nodes_by_role.return_value = []
        basic_manifest.core.software.charms.get.return_value = Mock(
            config={
                "log-level": "DEBUG",
                "role-mapping": "operator supplied mapping",
            }
        )

        step = DeployRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

        config = step.extra_tfvars()["charm_role_distributor_config"]

        assert config["log-level"] == "DEBUG"
        assert yaml.safe_load(config["role-mapping"]) == {
            "openstack-machines": {"microovn": {"machines": {}}}
        }

    def test_is_skip_skips_when_no_microovn_machines(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = []
        step = DeployRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_fails_when_targets_exist_without_control_machine(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = ["1"]
        basic_client.cluster.list_nodes_by_role.return_value = []
        step = DeployRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

        result = step.is_skip(step_context)

        assert result.result_type == ResultType.FAILED
        assert "control" in result.message


class TestReapplyRoleDistributorApplicationStep:
    def _create_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
    ):
        return ReapplyRoleDistributorApplicationStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "openstack-machines",
        )

    def test_run_skips_tf_apply_when_no_targets(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = []
        basic_client.cluster.list_nodes_by_role.return_value = []
        step = self._create_step(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.SKIPPED
        basic_tfhelper.update_tfvars_and_apply_tf.assert_not_called()
        basic_jhelper.wait_application_ready.assert_not_called()

    def test_run_waits_for_role_distributor_when_targets_remain(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = [
            "1",
            "2",
        ]
        basic_client.cluster.list_nodes_by_role.side_effect = lambda role: {
            "control": [{"machineid": "0", "role": ["control"]}],
            "compute": [{"machineid": "1", "role": ["compute"]}],
            "network": [{"machineid": "2", "role": ["network"]}],
        }.get(role, [])
        step = self._create_step(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        basic_jhelper.wait_application_ready.assert_called_once_with(
            "role-distributor",
            "openstack-machines",
            accepted_status=["active", "waiting"],
            timeout=600,
        )

        _, _, kwargs = basic_tfhelper.update_tfvars_and_apply_tf.mock_calls[0]
        role_mapping = yaml.safe_load(
            kwargs["override_tfvars"]["charm_role_distributor_config"]["role-mapping"]
        )
        assert role_mapping["openstack-machines"]["microovn"]["machines"] == {
            "1": {"roles": ["chassis"]},
            "2": {"roles": ["chassis", "gateway"]},
        }
        assert kwargs["override_tfvars"]["role_distributor_machine_ids"] == ["0"]

    def test_run_relocates_to_remaining_control_machine(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = [
            "2",
            "3",
        ]
        basic_client.cluster.list_nodes_by_role.side_effect = lambda role: {
            "control": [{"machineid": "2", "role": ["control"]}],
            "compute": [{"machineid": "3", "role": ["compute"]}],
            "network": [],
        }.get(role, [])
        step = self._create_step(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        _, _, kwargs = basic_tfhelper.update_tfvars_and_apply_tf.mock_calls[0]
        assert kwargs["override_tfvars"]["role_distributor_machine_ids"] == ["2"]

    def test_run_fails_when_targets_exist_without_control_machine(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        step_context,
    ):
        basic_deployment.get_ovn_manager.return_value.get_machines.return_value = ["1"]
        basic_client.cluster.list_nodes_by_role.side_effect = lambda role: {
            "control": [],
            "compute": [{"machineid": "1", "role": ["compute"]}],
            "network": [],
        }.get(role, [])
        step = self._create_step(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
        )

        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED
        assert "control" in result.message
        basic_tfhelper.update_tfvars_and_apply_tf.assert_not_called()
        basic_jhelper.wait_application_ready.assert_not_called()


class TestRemoveRoleDistributorUnitsStep:
    def test_remove_step_targets_role_distributor(
        self,
        basic_client,
        basic_jhelper,
    ):
        step = RemoveRoleDistributorUnitsStep(
            basic_client,
            "node-1",
            basic_jhelper,
            "openstack-machines",
        )

        assert step.config == "TerraformVarsRoleDistributorPlan"
        assert step.application == "role-distributor"
        assert step.names == ["node-1"]
