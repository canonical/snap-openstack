# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException, TimeoutException
from sunbeam.core.steps import (
    AddMachineUnitsStep,
    DeployMachineApplicationStep,
    RemoveMachineUnitsStep,
)
from sunbeam.core.terraform import TerraformException


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.core.steps.run_sync", run_sync)
    yield
    loop.close()


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
    yield AsyncMock()


@pytest.fixture()
def read_config():
    with patch("sunbeam.core.steps.read_config", return_value={}) as p:
        yield p


@pytest.fixture()
def manifest():
    yield Mock()


class TestDeployMachineApplicationStep:
    def test_is_skip(self, deployment, cclient, tfhelper, jhelper, manifest):
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
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_application_already_deployed(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
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
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_refresh(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
            refresh=True,
        )
        result = step.is_skip()

        jhelper.get_application.assert_not_called()
        assert result.result_type == ResultType.COMPLETED

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

        jhelper.get_application.assert_called_once()
        tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_deployed(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        tfconfig = "tfconfig"
        machines = ["1", "2"]
        model = "model1"
        application = Mock(units=[Mock(machine=Mock(id=m)) for m in machines])
        jhelper.get_application.return_value = application

        step = DeployMachineApplicationStep(
            deployment, cclient, tfhelper, jhelper, manifest, tfconfig, "app1", model
        )
        result = step.run()

        jhelper.get_application.assert_called_once()
        tfhelper.update_tfvars_and_apply_tf.assert_called_with(
            cclient,
            manifest,
            tfvar_config=tfconfig,
            override_tfvars={"machine_ids": machines, "machine_model": model},
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
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

    def test_run_waiting_timed_out(
        self, deployment, cclient, tfhelper, jhelper, manifest
    ):
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = DeployMachineApplicationStep(
            deployment,
            cclient,
            tfhelper,
            jhelper,
            manifest,
            "tfconfig",
            "app1",
            "model1",
            "fake-plan",
        )
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddMachineUnitsStep:
    def test_is_skip(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [
            {"name": "machine1", "machineid": "1"}
        ]
        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = []

        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message and "not exist in cluster database" in result.message

    def test_is_skip_application_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [
            {"name": "machine1", "machineid": "1"}
        ]
        jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application app1 has not been deployed"

    def test_is_skip_unit_already_deployed(self, cclient, jhelper):
        id = "1"
        cclient.cluster.list_nodes.return_value = [
            {"name": "machine1", "machineid": id}
        ]
        jhelper.get_application.return_value = Mock(units=[Mock(machine=Mock(id=id))])

        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self, cclient, jhelper, read_config):
        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, cclient, jhelper, read_config):
        jhelper.add_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        jhelper.add_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self, cclient, jhelper, read_config):
        jhelper.wait_until_desired_status.side_effect = TimeoutException("timed out")

        step = AddMachineUnitsStep(
            cclient, "machine1", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        jhelper.wait_until_desired_status.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveMachineUnitStep:
    def test_is_skip(self, cclient, jhelper):
        id = "1"
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": id}]
        jhelper.get_application.return_value = Mock(units=[Mock(machine=Mock(id=id))])

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.is_skip()

        cclient.cluster.list_nodes.assert_called_once()
        jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self, cclient, jhelper):
        cclient.cluster.list_nodes.return_value = [{"name": "node-0", "machineid": 1}]

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
        jhelper.get_application.return_value = Mock(units=[])

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
        jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = RemoveMachineUnitsStep(
            cclient, "node-0", jhelper, "tfconfig", "app1", "model1"
        )
        result = step.run()

        jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
