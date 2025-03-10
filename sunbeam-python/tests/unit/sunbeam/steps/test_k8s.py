# Copyright (c) 2024 Canonical Ltd. Canonical Ltd.
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
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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
    AddK8SCloudStep,
    AddK8SCredentialStep,
    StoreK8SKubeConfigStep,
)


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.steps.k8s.run_sync", run_sync)
    yield
    loop.close()


class TestAddK8SCloudStep(unittest.TestCase):
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

        step = AddK8SCloudStep(self.deployment, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_cloud_already_deployed(self):
        clouds = {f"cloud-{self.cloud_name}": {"endpoint": "10.0.10.1"}}
        self.jhelper.get_clouds.return_value = clouds

        step = AddK8SCloudStep(self.deployment, self.jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCloudStep(self.deployment, self.jhelper)
            result = step.run()

        self.jhelper.add_k8s_cloud.assert_called_with(
            self.cloud_name,
            f"{self.cloud_name}{CREDENTIAL_SUFFIX}",
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestAddK8SCredentialStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.deployment = Mock()
        self.deployment.name = "mydeployment"
        self.cloud_name = f"{self.deployment.name}{K8S_CLOUD_SUFFIX}"
        self.credential_name = f"{self.cloud_name}{CREDENTIAL_SUFFIX}"
        self.deployment.get_client().cluster.get_config.return_value = "{}"
        self.jhelper = AsyncMock()

    def test_is_skip(self):
        credentials = {}
        self.jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(self.deployment, self.jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_credential_exists(self):
        credentials = {"controller-credentials": {self.credential_name: {}}}
        self.jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(self.deployment, self.jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCredentialStep(self.deployment, self.jhelper)
            result = step.run()

        self.jhelper.add_k8s_credential.assert_called_with(
            self.cloud_name,
            self.credential_name,
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestStoreK8SKubeConfigStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.client = Mock(cluster=Mock(get_config=Mock(return_value="{}")))
        self.jhelper = AsyncMock()
        self.deployment = Mock()
        mock_machine = MagicMock()
        mock_machine.addresses = [
            {"value": "127.0.0.1:16443", "space-name": "management"}
        ]
        self.jhelper.get_machines.return_value = {"0": mock_machine}
        self.deployment.get_space.return_value = "management"

    def test_is_skip(self):
        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_config_missing(self):
        with patch(
            "sunbeam.steps.k8s.read_config",
            Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = StoreK8SKubeConfigStep(
                self.deployment, self.client, self.jhelper, "test-model"
            )
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self):
        kubeconfig_content = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: fakecert
    server: https://127.0.0.1:16443
  name: k8s-cluster
contexts:
- context:
    cluster: k8s-cluster
    user: admin
  name: k8s
current-context: k8s
kind: Config
preferences: {}
users:
- name: admin
  user:
    token: faketoken"""

        action_result = {
            "kubeconfig": kubeconfig_content,
        }
        self.jhelper.run_action.return_value = action_result
        self.jhelper.get_leader_unit.return_value = "k8s/0"

        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self):
        self.jhelper.get_leader_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_leader_not_found(self):
        self.jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )

        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Leader missing..."

    def test_run_action_failed(self):
        self.jhelper.run_action.side_effect = ActionFailedException("Action failed...")
        self.jhelper.get_leader_unit.return_value = "k8s/0"

        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."
