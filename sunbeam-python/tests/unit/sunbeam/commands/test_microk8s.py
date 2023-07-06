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

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    AddMicrok8sCloudStep,
    AddMicrok8sUnitStep,
    DeployMicrok8sApplicationStep,
    RemoveMicrok8sUnitStep,
    StoreMicrok8sConfigStep,
)
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    LeaderNotFoundException,
    TimeoutException,
)


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.microk8s.run_sync", run_sync)
    yield
    loop.close()


class TestDeployMicrok8sApplicationStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch("sunbeam.commands.microk8s.Client")
        self.snap = patch("sunbeam.commands.microk8s.Snap")

    def setUp(self):
        self.client.start()
        self.snap.start()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())

    def tearDown(self):
        self.client.stop()
        self.snap.stop()

    def test_is_skip(self):
        step = DeployMicrok8sApplicationStep(self.tfhelper, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployMicrok8sApplicationStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.jhelper.get_application.assert_called_once()
        self.tfhelper.write_tfvars.assert_called_with({"machine_ids": []})
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_already_deployed(self):
        machines = ["1", "2"]
        application = Mock(units=[Mock(machine=Mock(id=m)) for m in machines])
        self.jhelper.get_application.return_value = application

        step = DeployMicrok8sApplicationStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.jhelper.get_application.assert_called_once()
        self.tfhelper.write_tfvars.assert_called_with({"machine_ids": machines})
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = DeployMicrok8sApplicationStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = DeployMicrok8sApplicationStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddMicrok8sUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.clientMock = Mock()
        self.client = patch(
            "sunbeam.commands.microk8s.Client", return_value=self.clientMock
        )

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()
        self.name = "test-0"

    def tearDown(self):
        self.client.stop()
        self.clientMock.reset_mock()

    def test_is_skip(self):
        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.clientMock.cluster.get_node_info.side_effect = (
            NodeNotExistInClusterException("Node missing...")
        )

        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Node missing..."

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "MicroK8S has not been deployed"

    def test_is_skip_unit_already_deployed(self):
        id = "1"
        self.clientMock.cluster.get_node_info.return_value = {"machineid": id}
        self.jhelper.get_application.return_value = Mock(
            units=[Mock(machine=Mock(id=id))]
        )

        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.add_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.add_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self):
        self.jhelper.wait_unit_ready.side_effect = TimeoutException("timed out")

        step = AddMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_unit_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestRemoveMicrok8sUnitStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.clientMock = Mock()
        self.client = patch(
            "sunbeam.commands.microk8s.Client", return_value=self.clientMock
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

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_node_missing(self):
        self.clientMock.cluster.get_node_info.side_effect = (
            NodeNotExistInClusterException("Node missing...")
        )

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_application_missing(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_unit_missing(self):
        self.clientMock.cluster.get_node_info.return_value = {}
        self.jhelper.get_application.return_value = Mock(units=[])

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.is_skip()

        self.clientMock.cluster.get_node_info.assert_called_once()
        self.jhelper.get_application.assert_called_once()
        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.remove_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.remove_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_timeout(self):
        self.jhelper.wait_application_ready.side_effect = TimeoutException("timed out")

        step = RemoveMicrok8sUnitStep(self.name, self.jhelper)
        result = step.run()

        self.jhelper.wait_application_ready.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"


class TestAddMicrok8sCloudStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch(
            "sunbeam.commands.microk8s.Client",
            Mock(return_value=Mock(cluster=Mock(get_config=Mock(return_value="{}")))),
        )

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()

    def tearDown(self):
        self.client.stop()

    def test_is_skip(self):
        clouds = {}
        self.jhelper.get_clouds.return_value = clouds

        step = AddMicrok8sCloudStep(self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_cloud_already_deployed(self):
        clouds = {"cloud-sunbeam-microk8s": {"endpoint": "10.0.10.1"}}
        self.jhelper.get_clouds.return_value = clouds

        step = AddMicrok8sCloudStep(self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        with patch("sunbeam.commands.microk8s.read_config", Mock(return_value={})):
            step = AddMicrok8sCloudStep(self.jhelper)
            result = step.run()

        self.jhelper.add_k8s_cloud.assert_called_with(
            MICROK8S_CLOUD,
            f"{MICROK8S_CLOUD}{CREDENTIAL_SUFFIX}",
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestStoreMicrok8sConfigStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch(
            "sunbeam.commands.microk8s.Client",
            Mock(return_value=Mock(cluster=Mock(get_config=Mock(return_value="{}")))),
        )

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()

    def tearDown(self):
        self.client.stop()

    def test_is_skip(self):
        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_config_missing(self):
        with patch(
            "sunbeam.commands.microk8s.read_config",
            Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = StoreMicrok8sConfigStep(self.jhelper)
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self):
        kubeconfig_content = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: fakecert
    server: https://127.0.0.1:16443
  name: microk8s-cluster
contexts:
- context:
    cluster: microk8s-cluster
    user: admin
  name: microk8s
current-context: microk8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    token: faketoken"""

        action_result = {
            "kubeconfig": "/home/ubuntu/config",
            "content": kubeconfig_content,
        }
        self.jhelper.run_action.return_value = action_result

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.get_leader_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_leader_not_found(self):
        self.jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Leader missing..."

    def test_run_action_failed(self):
        self.jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."
