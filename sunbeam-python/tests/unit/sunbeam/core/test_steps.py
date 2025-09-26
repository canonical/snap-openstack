# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import Mock, patch

import pytest
import tenacity

from sunbeam.core.common import ResultType, Role
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.steps import (
    DeployMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException, TerraformStateLockedException


@pytest.fixture()
def deployment():
    yield Mock()


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def tfhelper():
    yield Mock()


@pytest.fixture()
def jhelper():
    yield Mock()


@pytest.fixture()
def read_config():
    with patch("sunbeam.core.steps.read_config", return_value={}) as p:
        yield p


@pytest.fixture()
def manifest():
    yield Mock()


class TestDeployMachineApplicationStep:
    def test_run_pristine_installation(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        jhelper.get_application.side_effect = ApplicationNotFoundException("not found")

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_deployed(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        tfconfig = "tfconfig"
        machines = ["1", "2"]
        model = "model1"
        cclient.cluster.list_nodes_by_role.return_value = [
            {"name": "machine1", "machineid": "1"},
            {"name": "machine1", "machineid": "2"},
        ]

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            tfconfig,
            "app1",
            model,
            [Role.CONTROL],
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            cclient,
            manifest,
            tfvar_config=tfconfig,
            override_tfvars={"machine_ids": machines, "machine_model": model},
            tf_apply_extra_args=[],
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_tf_apply_locked(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        tfhelper.update_tfvars_and_apply_tf.side_effect = [
            TerraformStateLockedException("apply failed..."),
            None,
        ]

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        step.run.retry.wait = tenacity.wait_none()
        result = step.run()

        tfhelper.update_tfvars_and_apply_tf.assert_called()
        assert result.result_type == ResultType.COMPLETED

    def test_run_waiting_timed_out(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        jhelper.get_application.return_value = Mock(units={"app/1": Mock(machine=1)})
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
        )
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveMachineUnitStep:
    def test_is_skip(self, cclient, jhelper):
        id = "1"
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": id}]
        jhelper.get_application.return_value = Mock(units={"app1/0": Mock(machine=id)})

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]

        jhelper.get_application.return_value = Mock(units={"app1/0": Mock()})
        step = RemoveMachineUnitsStep(
            cclient, "node-1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]
        jhelper.get_application.return_value = Mock(units={})

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, jhelper, read_config):
        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, cclient, jhelper, read_config):
        jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        step.units_to_remove = {"app1/0"}
        result = step.run()

        jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self, cclient, jhelper, read_config):
        jhelper.wait_application_ready.side_effect = TimeoutError("timed out")

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
