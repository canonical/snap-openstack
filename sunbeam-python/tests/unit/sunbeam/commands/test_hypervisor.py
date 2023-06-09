# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.hypervisor import (
    AddHypervisorUnitStep,
    DeployHypervisorApplicationStep,
    RemoveHypervisorUnitStep,
)
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import ApplicationNotFoundException, TimeoutException


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.hypervisor.run_sync", run_sync)
    yield
    loop.close()


class TestDeployHypervisorStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch("sunbeam.commands.hypervisor.Client")

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.tfhelper_openstack = Mock(output=Mock(return_value={}))

    def tearDown(self):
        self.client.stop()

    def test_is_skip(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployHypervisorApplicationStep(
            self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_app_already_deployed(self):
        step = DeployHypervisorApplicationStep(
            self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployHypervisorApplicationStep(
            self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.tfhelper.write_tfvars.assert_called_once()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = DeployHypervisorApplicationStep(
            self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = DeployHypervisorApplicationStep(
            self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddHypervisorUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.clientMock = Mock()
        self.client = patch(
            "sunbeam.commands.hypervisor.Client", return_value=self.clientMock
        )

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def tearDown(self):
        self.client.stop()
        self.clientMock.reset_mock()

    def test_is_skip(self):
        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.clientMock.cluster.get_node_info.side_effect = (
            NodeNotExistInClusterException("Node missing...")
        )

        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Node missing..."

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.FAILED
        msg = "openstack-hypervisor application has not been deployed yet"
        assert result.message == msg

    def test_is_skip_unit_already_deployed(self):
        id = "1"
        self.clientMock.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units=[Mock(machine=Mock(id=id))]
        )

        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.add_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.add_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self):
        self.jhelper.wait_unit_ready.side_effect = TimeoutException("timed out")

        step = AddHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_unit_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveHypervisorUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.clientMock = Mock()
        self.client = patch(
            "sunbeam.commands.hypervisor.Client", return_value=self.clientMock
        )

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def tearDown(self):
        self.client.stop()
        self.clientMock.reset_mock()

    def test_is_skip(self):
        id = "1"
        self.clientMock.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units=[Mock(machine=Mock(id=id))]
        )

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.clientMock.cluster.get_node_info.side_effect = (
            NodeNotExistInClusterException("Node missing...")
        )

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self):
        self.clientMock.cluster.get_node_info.return_value = {}
        self.jhelper.get_application.return_value = Mock(units=[])

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = RemoveHypervisorUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
