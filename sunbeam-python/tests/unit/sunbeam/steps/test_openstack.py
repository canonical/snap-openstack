# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import unittest
from unittest.mock import MagicMock, Mock, patch

import pytest
import tenacity

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ApplicationNotFoundException,
    JujuWaitException,
)
from sunbeam.core.k8s import (
    METALLB_ADDRESS_POOL_ANNOTATION,
    METALLB_ALLOCATED_POOL_ANNOTATION,
    METALLB_IP_ANNOTATION,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.openstack import REGION_CONFIG_KEY
from sunbeam.core.terraform import TerraformException
from sunbeam.steps.openstack import (
    DATABASE_MEMORY_KEY,
    DEFAULT_STORAGE_MULTI_DATABASE,
    DEFAULT_STORAGE_SINGLE_DATABASE,
    DeployControlPlaneStep,
    OpenStackPatchLoadBalancerServicesIPPoolStep,
    OpenStackPatchLoadBalancerServicesIPStep,
    ReapplyOpenStackTerraformPlanStep,
    compute_ha_scale,
    compute_ingress_scale,
    compute_os_api_scale,
    get_database_default_storage_dict,
    get_database_storage_dict,
)

TOPOLOGY = "single"
MODEL = "test-model"


class TestDeployControlPlaneStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.snap_mock = Mock()
        self.snap = patch("sunbeam.core.k8s.Snap", self.snap_mock)

    def setUp(self):
        self.jhelper = Mock()
        self.jhelper.run_action.return_value = {}
        self.tfhelper = Mock()
        self.manifest = MagicMock()
        self.client = Mock()
        self.deployment = Mock()
        self.deployment.get_client.return_value = self.client
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
        self.jhelper.get_application_names.return_value = ["app1"]
        self.jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployControlPlaneStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
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
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        self.tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.jhelper.get_application_names.return_value = ["app1"]
        self.jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        step = DeployControlPlaneStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.snap_mock().config.get.return_value = "k8s"
        self.jhelper.get_application_names.return_value = ["app1"]
        self.jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = DeployControlPlaneStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"

    def test_is_skip_pristine(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            MODEL,
        )
        with patch(
            "sunbeam.steps.openstack.read_config",
            Mock(side_effect=ConfigItemNotFoundException("not found")),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_subsequent_run(self):
        self.snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            self.deployment,
            self.tfhelper,
            self.jhelper,
            self.manifest,
            TOPOLOGY,
            MODEL,
        )
        with patch(
            "sunbeam.steps.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED


class PatchLoadBalancerServicesIPStepTest(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.read_config = patch(
            "sunbeam.core.steps.read_config",
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
        self.snap = patch("sunbeam.core.k8s.Snap", self.snap_mock)

    def setUp(self):
        self.client = Mock()
        self.client.cluster.list_nodes_by_role.return_value = ["node-1"]
        self.read_config.start()
        self.snap.start()

    def tearDown(self):
        self.read_config.stop()
        self.snap.stop()

    def test_is_skip(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(
                                annotations={METALLB_IP_ANNOTATION: "fake-ip"}
                            )
                        )
                    )
                )
            ),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_missing_annotation(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(return_value=Mock(metadata=Mock(annotations={})))
                )
            ),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_missing_config(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(self.client)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
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
            step = OpenStackPatchLoadBalancerServicesIPStep(self.client)
            step.is_skip()
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_IP_ANNOTATION
        ]
        assert annotation == "fake-ip"


class PatchLoadBalancerServicesIPPoolStepTest(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.pool_name = "fake-pool"
        self.read_config = patch(
            "sunbeam.core.steps.read_config",
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
        self.snap = patch("sunbeam.core.k8s.Snap", self.snap_mock)

    def setUp(self):
        self.client = Mock()
        self.client.cluster.list_nodes_by_role.return_value = ["node-1"]
        self.read_config.start()
        self.snap.start()

    def tearDown(self):
        self.read_config.stop()
        self.snap.stop()

    def test_run(self):
        self.snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(
                self.client, self.pool_name
            )
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == self.pool_name

    def test_run_missing_annotation(self):
        self.snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(metadata=Mock(annotations={})),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(
                self.client, self.pool_name
            )
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == self.pool_name

    def test_run_missing_config(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(
                self.client, self.pool_name
            )
            result = step.run()
        assert result.result_type == ResultType.FAILED

    def test_run_same_ippool_already_allocation(self):
        self.snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(
                                annotations={
                                    METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                                    METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                                }
                            )
                        )
                    )
                )
            ),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(
                self.client, self.pool_name
            )
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        step.kube.patch.assert_not_called()

    def test_run_different_ippool_already_allocated(self):
        self.snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: "another-pool",
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: "another-pool",
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: self.pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: self.pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(
                self.client, self.pool_name
            )
            step._wait_for_ip_allocated_from_pool_annotation_update.retry.wait = (
                tenacity.wait_none()
            )
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == self.pool_name


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
            "sunbeam.steps.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        )

    def setUp(self):
        self.client = Mock(
            cluster=Mock(list_nodes_by_role=Mock(return_value=[1, 2, 3, 4]))
        )
        self.read_config.start()
        self.tfhelper = Mock()
        self.jhelper = Mock()
        self.manifest = Mock()

    def tearDown(self):
        self.read_config.stop()

    def test_run(self):
        self.jhelper.get_application_names.return_value = ["placement", "nova-compute"]
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
        self.jhelper.get_application_names.return_value = ["placement", "nova-compute"]
        self.jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        step = ReapplyOpenStackTerraformPlanStep(
            self.client, self.tfhelper, self.jhelper, self.manifest
        )
        result = step.run()

        self.jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(self):
        self.jhelper.get_application_names.return_value = ["placement", "nova-compute"]
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


@pytest.fixture()
def read_config():
    with patch("sunbeam.steps.openstack.read_config") as p:
        yield p


@pytest.mark.parametrize(
    "many_mysql,clusterdb_configs,manifest,expected_storage",
    [
        # Defaults with no clusterdb and manifest
        (False, {}, {}, {"mysql": DEFAULT_STORAGE_SINGLE_DATABASE}),
        (True, {}, {}, DEFAULT_STORAGE_MULTI_DATABASE),
        # Values from clusterdb takes precedence when manifest is empty
        (False, {"mysql": "1G"}, {}, {"mysql": "1G"}),
        (True, {"nova": "10G"}, {}, {"nova": "10G"}),
        (True, {"neutron": "10G"}, {}, {"nova": "10G", "neutron": "10G"}),
        # Values from manifest as manifest takes precedence
        (
            False,
            {"mysql": "1G"},
            {"core": {"software": {"charms": {"mysql-k8s": {}}}}},
            {"mysql": "1G"},
        ),
        (
            False,
            {"mysql": "1G"},
            {
                "core": {
                    "software": {
                        "charms": {"mysql-k8s": {"storage": {"database": "20G"}}}
                    }
                }
            },
            {"mysql": "20G"},
        ),
        (
            True,
            {"nova": "1G"},
            {"core": {"software": {"charms": {"mysql-k8s": {}}}}},
            {"nova": "1G"},
        ),
        (
            True,
            {"nova": "1G"},
            {
                "core": {
                    "software": {
                        "charms": {
                            "mysql-k8s": {
                                "storage-map": {
                                    "nova": {"database": "20G"},
                                    "keystone": {"database": "10G"},
                                }
                            }
                        }
                    }
                }
            },
            {"nova": "20G", "keystone": "10G"},
        ),
    ],
)
def test_get_database_storage_dict(
    read_config, snap, many_mysql, clusterdb_configs, manifest, expected_storage
):
    client = Mock()
    read_config.return_value = clusterdb_configs
    manifest = Manifest(**manifest)
    default_storages = get_database_default_storage_dict(many_mysql)
    storages = get_database_storage_dict(client, many_mysql, manifest, default_storages)
    assert storages == expected_storage
