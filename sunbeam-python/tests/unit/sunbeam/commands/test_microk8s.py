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
from snaphelpers import Snap

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.microk8s import (
    AddMicrok8sCloudStep,
    DeployMicrok8sAddonsStep,
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


class TestAddMicrok8sCloudStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch(
            "sunbeam.commands.microk8s.Client",
            Mock(return_value=Mock(cluster=Mock(get_config=Mock(return_value={})))),
        )
        self.read_config = patch(
            "sunbeam.commands.microk8s.read_config",
            Mock(return_value={}),
        )
        snap_env = {
            "SNAP": "/snap/mysnap/2",
            "SNAP_COMMON": "/var/snap/mysnap/common",
            "SNAP_DATA": "/var/snap/mysnap/2",
            "SNAP_INSTANCE_NAME": "",
            "SNAP_NAME": "mysnap",
            "SNAP_REVISION": "2",
            "SNAP_USER_COMMON": "",
            "SNAP_USER_DATA": "",
            "SNAP_VERSION": "1.2.3",
            "SNAP_REAL_HOME": "/home/ubuntu",
        }
        self.snap = patch(
            "sunbeam.commands.juju.Snap", Mock(return_value=Snap(environ=snap_env))
        )
        self.subprocess = patch("sunbeam.commands.juju.subprocess.run")

    def setUp(self):
        self.client.start()
        self.jhelper = AsyncMock()
        self.read_config.start()
        self.snap.start()
        self.subprocess.start()

    def tearDown(self):
        self.client.stop()
        self.read_config.stop()
        self.snap.stop()
        self.subprocess.stop()

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
        step = AddMicrok8sCloudStep(self.jhelper)
        result = step.run()

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
            "stdout": kubeconfig_content,
        }
        self.jhelper.run_command.return_value = action_result

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_command.assert_called_once()
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

    def test_run_command_failed(self):
        self.jhelper.run_command.side_effect = ActionFailedException("Action failed...")

        step = StoreMicrok8sConfigStep(self.jhelper)
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_command.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."


class TestDeployMicrok8sAddonsStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = patch("sunbeam.commands.microk8s.Client")
        self.read_config = patch(
            "sunbeam.commands.microk8s.read_config",
            Mock(return_value={}),
        )

    def setUp(self):
        self.client.start()
        self.read_config.start()
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())

    def tearDown(self):
        self.client.stop()
        self.read_config.stop()

    def test_run(self):
        step = DeployMicrok8sAddonsStep(self.tfhelper, self.jhelper)
        result = step.run()

        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = DeployMicrok8sAddonsStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_timeout(self):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = DeployMicrok8sAddonsStep(self.tfhelper, self.jhelper)
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"
