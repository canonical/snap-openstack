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
    ReapplyHypervisorTerraformPlanStep,
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
        self.read_config = patch(
            "sunbeam.commands.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.tfhelper_openstack = Mock(output=Mock(return_value={}))
        self.tfhelper_openstack.backend = "http"
        self.tfhelper_openstack.backend_config.return_value = {}

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployHypervisorApplicationStep(
            self.client, self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_app_already_deployed(self):
        step = DeployHypervisorApplicationStep(
            self.client, self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployHypervisorApplicationStep(
            self.client, self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.tfhelper.write_tfvars.assert_called_once()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = DeployHypervisorApplicationStep(
            self.client, self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = DeployHypervisorApplicationStep(
            self.client, self.tfhelper, self.tfhelper_openstack, self.jhelper
        )
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddHypervisorUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.commands.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Node missing..."

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.FAILED
        msg = "openstack-hypervisor application has not been deployed yet"
        assert result.message == msg

    def test_is_skip_unit_already_deployed(self):
        id = "1"
        self.client.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units=[Mock(machine=Mock(id=id))]
        )

        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.add_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()

        self.jhelper.add_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self):
        self.jhelper.wait_unit_ready.side_effect = TimeoutException("timed out")

        step = AddHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_unit_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveHypervisorUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.commands.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )
        guest = Mock()
        type(guest).name = "my-guest"
        self.guests = [guest]

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        id = "1"
        self.client.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units=[Mock(machine=Mock(id=id))]
        )

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "Node missing..."
        )

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self):
        self.client.cluster.get_node_info.return_value = {}
        self.jhelper.get_application.return_value = Mock(units=[])

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.is_skip()

        self.client.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.commands.hypervisor.remove_hypervisor")
    @patch("sunbeam.commands.hypervisor.guests_on_hypervisor")
    def test_run(self, guests_on_hypervisor, remove_hypervisor):
        guests_on_hypervisor.return_value = []
        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", self.jhelper)

    @patch("sunbeam.commands.hypervisor.remove_hypervisor")
    @patch("sunbeam.commands.hypervisor.guests_on_hypervisor")
    def test_run_guests(self, guests_on_hypervisor, remove_hypervisor):
        guests_on_hypervisor.return_value = self.guests
        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert not remove_hypervisor.called

    @patch("sunbeam.commands.hypervisor.remove_hypervisor")
    @patch("sunbeam.commands.hypervisor.guests_on_hypervisor")
    def test_run_guests_force(self, guests_on_hypervisor, remove_hypervisor):
        guests_on_hypervisor.return_value = self.guests
        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper, True)
        result = step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_hypervisor.assert_called_once_with("test-0", self.jhelper)

    @patch("sunbeam.commands.hypervisor.remove_hypervisor")
    @patch("sunbeam.commands.hypervisor.guests_on_hypervisor")
    def test_run_application_not_found(self, guests_on_hypervisor, remove_hypervisor):
        guests_on_hypervisor.return_value = []
        self.jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()

        self.jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    @patch("sunbeam.commands.hypervisor.remove_hypervisor")
    @patch("sunbeam.commands.hypervisor.guests_on_hypervisor")
    def test_run_timeout(self, guests_on_hypervisor, remove_hypervisor):
        guests_on_hypervisor.return_value = []
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = RemoveHypervisorUnitStep(self.client, self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestReapplyHypervisorTerraformPlanStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.commands.hypervisor.read_config",
            Mock(
                return_value={
                    "openstack_model": "openstack",
                }
            ),
        )

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.tfhelper_openstack = Mock(output=Mock(return_value={}))
        self.tfhelper_openstack.backend = "http"
        self.tfhelper_openstack.backend_config.return_value = {}

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper
        )
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper
        )
        result = step.run()

        self.tfhelper.write_tfvars.assert_called_once()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper
        )
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = ReapplyHypervisorTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper
        )
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
