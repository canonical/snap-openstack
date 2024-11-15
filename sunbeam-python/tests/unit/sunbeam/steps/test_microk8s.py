# Copyright (c) 2023 Canonical Ltd.
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
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    LeaderNotFoundException,
)
from sunbeam.steps.k8s import (
    CREDENTIAL_SUFFIX,
    K8S_CLOUD_SUFFIX,
)
from sunbeam.steps.microk8s import (
    AddMicrok8sCloudStep,
    StoreMicrok8sConfigStep,
)


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.steps.microk8s.run_sync", run_sync)
    yield
    loop.close()


class TestAddMicrok8sCloudStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.deployment = Mock()
        self.cloud_name = f"{self.deployment.name}{K8S_CLOUD_SUFFIX}"
        self.deployment.get_client().cluster.get_config.return_value = "{}"
        self.jhelper = AsyncMock()

    def test_is_skip(self):
        clouds = {}
        self.jhelper.get_clouds.return_value = clouds

        step = AddMicrok8sCloudStep(self.deployment, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_cloud_already_deployed(self):
        clouds = {f"cloud-{self.cloud_name}": {"endpoint": "10.0.10.1"}}
        self.jhelper.get_clouds.return_value = clouds

        step = AddMicrok8sCloudStep(self.deployment, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        with patch("sunbeam.steps.microk8s.read_config", Mock(return_value={})):
            step = AddMicrok8sCloudStep(self.deployment, self.jhelper)
            result = step.run()

        self.jhelper.add_k8s_cloud.assert_called_with(
            self.cloud_name,
            f"{self.cloud_name}{CREDENTIAL_SUFFIX}",
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestStoreMicrok8sConfigStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.client = Mock(cluster=Mock(get_config=Mock(return_value="{}")))
        self.jhelper = AsyncMock()

    def test_is_skip(self):
        step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_config_missing(self):
        with patch(
            "sunbeam.steps.microk8s.read_config",
            Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
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

        step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.get_leader_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_leader_not_found(self):
        self.jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )

        step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Leader missing..."

    def test_run_action_failed(self):
        self.jhelper.run_action.side_effect = ActionFailedException("Action failed...")

        step = StoreMicrok8sConfigStep(self.client, self.jhelper, "test-model")
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."
