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

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.openstack import (
    METALLB_ANNOTATION,
    DeployControlPlaneStep,
    PatchLoadBalancerServicesStep,
    ResizeControlPlaneStep,
    compute_ceph_replica_scale,
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

    def setUp(self):
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())
        self.client = Mock()

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={}),
        ):
            result = step.run()

        self.tfhelper.write_tfvars.assert_called_once()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={}),
        ):
            result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={}),
        ):
            result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={}),
        ):
            result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"

    def test_is_skip_pristine(self):
        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(side_effect=ConfigItemNotFoundException("not found")),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_subsequent_run(self):
        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_database_changed(self):
        step = DeployControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, DATABASE
        )
        with patch(
            "sunbeam.commands.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "multi"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.FAILED


class TestResizeControlPlaneStep(unittest.TestCase):
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
        self.jhelper = AsyncMock()
        self.tfhelper = Mock(path=Path())

    def tearDown(self):
        self.read_config.stop()

    def test_run_pristine_installation(self):
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, "single", False
        )
        result = step.run()

        self.tfhelper.write_tfvars.assert_called_once()
        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(self):
        self.tfhelper.apply.side_effect = TerraformException("apply failed...")

        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, False
        )
        result = step.run()

        self.tfhelper.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.jhelper.wait_until_active.side_effect = TimeoutException("timed out")

        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, False
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, TOPOLOGY, False
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"

    def test_run_incompatible_topology(self):
        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, "large", False
        )
        result = step.run()

        assert result.result_type == ResultType.FAILED
        assert "Cannot resize control plane to large" in result.message

    def test_run_force_incompatible_topology(self):
        step = ResizeControlPlaneStep(
            self.client, self.tfhelper, self.jhelper, "large", True
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.COMPLETED


class PatchLoadBalancerServicesStepTest(unittest.TestCase):
    """"""

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

    def setUp(self):
        self.client = Mock()
        self.read_config.start()

    def tearDown(self):
        self.read_config.stop()

    def test_is_skip(self):
        with patch(
            "sunbeam.commands.openstack.KubeClient",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(annotations={METALLB_ANNOTATION: "fake-ip"})
                        )
                    )
                )
            ),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_missing_annotation(self):
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
        with patch(
            "sunbeam.commands.openstack.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = PatchLoadBalancerServicesStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self):
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
            METALLB_ANNOTATION
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
        ("multi", 9, 9),
        ("large", 4, 4),
        ("large", 9, 9),
    ],
)
def test_compute_ingress_scale(topology, control_nodes, scale):
    assert compute_ingress_scale(topology, control_nodes) == scale


@pytest.mark.parametrize(
    "topology,storage_nodes,scale",
    [
        ("single", 1, 1),
        ("multi", 1, 1),
        ("multi", 9, 3),
        ("multi", 2, 2),
    ],
)
def test_compute_ceph_replica_scale(topology, storage_nodes, scale):
    assert compute_ceph_replica_scale(topology, storage_nodes) == scale
