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
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.k8s import SERVICE_LB_ANNOTATION
from sunbeam.commands.openstack import (
    DATABASE_MEMORY_KEY,
    REGION_CONFIG_KEY,
    DeployControlPlaneStep,
    PatchLoadBalancerServicesStep,
    ReapplyOpenStackTerraformPlanStep,
    compute_ha_scale,
    compute_ingress_scale,
    compute_os_api_scale,
)
from sunbeam.commands.terraform import TerraformException
from sunbeam.jobs.common import ResultType
from sunbeam.jobs.juju import (
    ApplicationNotFoundException,
    JujuWaitException,
    TimeoutException,
)

TOPOLOGY = "single"
DATABASE = "single"
MODEL = "test-model"


@pytest.fixture(autouse=True)
def mock_run_sync(mocker):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    def run_sync(coro):
        return loop.run_until_complete(coro)

    mocker.patch("sunbeam.commands.openstack.run_sync", run_sync)
    yield
    loop.close()


class TestDeployControlPlaneStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.snap_mock = Mock()
        self.snap = patch("sunbeam.commands.k8s.Snap", self.snap_mock)

    def setUp(self):
        self.jhelper = AsyncMock()
        self.jhelper.run_action.return_value = {}
        self.tfhelper = Mock()
        self.manifest = Mock()
        self.client = Mock()
        self.client.cluster.list_nodes_by_role.side_effect = [
            [{"name": f"control-{i}"} for i in range(4)],
            [{"name": f"storage-{i}"} for i in range(4)],
        ]
        self.configs = {
            REGION_CONFIG_KEY: json.dumps(
                {
                    "region": "TestOne",
                }
            ),
            DATABASE_MEMORY_KEY: json.dumps({}),
        }

        def _read_config_mock(key):
            if value := self.configs.get(key):
                return value
            raise ConfigItemNotFoundException(f"Config item {key} not found")

        self.client.cluster.get_config.side_effect = _read_config_mock

        self.snap.start()

    def tearDown(self):
        self.snap.stop()

    def test_run_pristine_installation(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"

    def test_is_skip_pristine(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(side_effect=ConfigItemNotFoundException("not found")),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_subsequent_run(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_database_changed(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            DATABASE,
            MODEL,
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "multi"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.FAILED

    def test_is_skip_incompatible_topology(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            "large",
            "auto",
            MODEL,
            force=False,
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.FAILED
        assert result.message
        assert "use -f/--force to override" in result.message

    def test_is_skip_force_incompatible_topology(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.client,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            "large",
            "auto",
            MODEL,
            force=True,
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED


class PatchLoadBalancerServicesStepTest(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.commands.openstack.read_config",
            Mock(
                return_value={
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
            ),
        )
        self.snap_mock = Mock()
        self.snap = patch("sunbeam.commands.k8s.Snap", self.snap_mock)

    def setUp(self):
        self.client = Mock()
        self.read_config.start()
        self.snap.start()

    def tearDown(self):
        self.read_config.stop()
        self.snap.stop()

    def test_is_skip(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.commands.openstack.KubeClient",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(
                                annotations={SERVICE_LB_ANNOTATION: "fake-ip"}
                            )
                        )
                    )
                )
            ),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_missing_annotation(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.commands.openstack.KubeClient",
            new=Mock(
                return_value=Mock(
                    get=Mock(return_value=Mock(metadata=Mock(annotations={})))
                )
            ),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_missing_config(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.commands.openstack.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.commands.openstack.KubeClient",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(annotations={}),
                            status=Mock(
                                loadBalancer=Mock(ingress=[Mock(ip="fake-ip")])
                            ),
                        )
                    )
                )
            ),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            step.is_skip()
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            SERVICE_LB_ANNOTATION
        ]
        assert annotation == "fake-ip"


@pytest.mark.parametrize(
    "topology,control_nodes,scale",
    [
        ("single", 1, 1),
        ("multi", 2, 1),
        ("multi", 3, 3),
        ("multi", 9, 3),
        ("large", 9, 3),
    ],
)
def test_compute_ha_scale(topology, control_nodes, scale):
    assert compute_ha_scale(topology, control_nodes) == scale


@pytest.mark.parametrize(
    "topology,control_nodes,scale",
    [
        ("single", 1, 1),
        ("multi", 2, 2),
        ("multi", 3, 3),
        ("multi", 9, 3),
        ("large", 4, 6),
        ("large", 9, 7),
    ],
)
def test_compute_os_api_scale(topology, control_nodes, scale):
    assert compute_os_api_scale(topology, control_nodes) == scale


@pytest.mark.parametrize(
    "topology,control_nodes,scale",
    [
        ("single", 1, 1),
        ("multi", 2, 2),
        ("multi", 3, 3),
        ("multi", 9, 3),
        ("large", 4, 3),
        ("large", 9, 3),
    ],
)
def test_compute_ingress_scale(topology, control_nodes, scale):
    assert compute_ingress_scale(topology, control_nodes) == scale


class TestReapplyOpenStackTerraformPlanStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        )

    def setUp(self):
        self.client = Mock(
            cluster=Mock(list_nodes_by_role=Mock(return_value=[1, 2, 3, 4]))
        )
        self.read_config.start()
        self.tfhelper = Mock()
        self.jhelper = AsyncMock()
        self.manifest = Mock()

    def tearDown(self):
        self.read_config.stop()

    def test_run(self):
        step = ReapplyOpenStackTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = ReapplyOpenStackTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = ReapplyOpenStackTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = ReapplyOpenStackTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"
