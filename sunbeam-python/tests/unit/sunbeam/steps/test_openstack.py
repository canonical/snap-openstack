# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

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
from sunbeam.core.openstack import ENDPOINTS_CONFIG_KEY, REGION_CONFIG_KEY
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


# Additional fixtures specific to openstack tests
@pytest.fixture
def basic_client():
    """Basic client mock."""
    client = Mock()
    client.cluster.list_nodes_by_role.side_effect = [
        [{"name": f"control-{i}"} for i in range(4)],
        [{"name": f"storage-{i}"} for i in range(4)],
        [],
    ]
    return client


@pytest.fixture
def deployment_with_client(basic_client):
    """Deployment mock with configured client."""
    deployment = Mock()
    deployment.get_client.return_value = basic_client
    return deployment


@pytest.fixture
def config_mock(basic_client):
    """Mock configuration data."""
    configs = {
        REGION_CONFIG_KEY: json.dumps(
            {
                "region": "TestOne",
            }
        ),
        DATABASE_MEMORY_KEY: json.dumps({}),
        ENDPOINTS_CONFIG_KEY: json.dumps({}),
    }

    def _read_config_mock(key):
        if value := configs.get(key):
            return value
        raise ConfigItemNotFoundException(f"Config item {key} not found")

    basic_client.cluster.get_config.side_effect = _read_config_mock
    return configs


@pytest.fixture
def read_config_patch():
    """Patch for read_config in steps."""
    with patch(
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
    ) as mock:
        yield mock


class TestDeployControlPlaneStep:
    def test_run_pristine_installation(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        basic_jhelper.get_application_names.return_value = ["app1"]
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        basic_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        basic_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        basic_jhelper.get_application_names.return_value = ["app1"]
        basic_jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        basic_jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        basic_jhelper.get_application_names.return_value = ["app1"]
        basic_jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        result = step.run()

        basic_jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Unit in error: placement/0"

    def test_is_skip_pristine(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        with patch(
            "sunbeam.steps.openstack.read_config",
            Mock(side_effect=ConfigItemNotFoundException("not found")),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_subsequent_run(
        self,
        deployment_with_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        config_mock,
        snap_patch,
        snap_mock,
    ):
        snap_mock().config.get.return_value = "k8s"
        step = DeployControlPlaneStep(
            deployment_with_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            TOPOLOGY,
            MODEL,
        )
        with patch(
            "sunbeam.steps.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED


class PatchLoadBalancerServicesIPStepTest:
    @pytest.fixture
    def patch_client(self):
        """Client for patch tests."""
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = ["node-1"]
        return client

    def test_is_skip(self, patch_client, read_config_patch, snap_patch, snap_mock):
        snap_mock().config.get.return_value = "k8s"
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
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_missing_annotation(
        self, patch_client, read_config_patch, snap_patch, snap_mock
    ):
        snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(return_value=Mock(metadata=Mock(annotations={})))
                )
            ),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client)
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_missing_config(self, patch_client, snap_patch, snap_mock):
        snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(self, patch_client, read_config_patch, snap_patch, snap_mock):
        snap_mock().config.get.return_value = "k8s"
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
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client)
            step.is_skip()
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_IP_ANNOTATION
        ]
        assert annotation == "fake-ip"


class PatchLoadBalancerServicesIPPoolStepTest:
    @pytest.fixture
    def pool_name(self):
        """Pool name for testing."""
        return "fake-pool"

    @pytest.fixture
    def pool_client(self):
        """Client for pool tests."""
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = ["node-1"]
        return client

    def test_run(
        self, pool_client, pool_name, read_config_patch, snap_patch, snap_mock
    ):
        snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(pool_client, pool_name)
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == pool_name

    def test_run_missing_annotation(
        self, pool_client, pool_name, read_config_patch, snap_patch, snap_mock
    ):
        snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(metadata=Mock(annotations={})),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(pool_client, pool_name)
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == pool_name

    def test_run_missing_config(self, pool_client, pool_name, snap_patch, snap_mock):
        snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(pool_client, pool_name)
            result = step.run()
        assert result.result_type == ResultType.FAILED

    def test_run_same_ippool_already_allocation(
        self, pool_client, pool_name, read_config_patch, snap_patch, snap_mock
    ):
        snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(
                return_value=Mock(
                    get=Mock(
                        return_value=Mock(
                            metadata=Mock(
                                annotations={
                                    METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                                    METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                                }
                            )
                        )
                    )
                )
            ),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(pool_client, pool_name)
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        step.kube.patch.assert_not_called()

    def test_run_different_ippool_already_allocated(
        self, pool_client, pool_name, read_config_patch, snap_patch, snap_mock
    ):
        snap_mock().config.get.return_value = "k8s"
        kube_get_mock = Mock()
        kube_get_mock.side_effect = [
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: "another-pool",
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: "another-pool",
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
            Mock(
                metadata=Mock(
                    annotations={
                        METALLB_ADDRESS_POOL_ANNOTATION: pool_name,
                        METALLB_ALLOCATED_POOL_ANNOTATION: pool_name,
                    }
                )
            ),
        ]
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=kube_get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPPoolStep(pool_client, pool_name)
            step._wait_for_ip_allocated_from_pool_annotation_update.retry.wait = (
                tenacity.wait_none()
            )
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        annotation = step.kube.patch.mock_calls[0][2]["obj"].metadata.annotations[
            METALLB_ADDRESS_POOL_ANNOTATION
        ]
        assert annotation == pool_name


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


class TestReapplyOpenStackTerraformPlanStep:
    @pytest.fixture
    def openstack_read_config_patch(self):
        """Patch for read_config in openstack steps."""
        with patch(
            "sunbeam.steps.openstack.read_config",
            Mock(return_value={"topology": "single", "database": "single"}),
        ) as mock:
            yield mock

    @pytest.fixture
    def openstack_client(self):
        """Client for openstack reapply tests."""
        return Mock(cluster=Mock(list_nodes_by_role=Mock(return_value=[1, 2, 3, 4])))

    @pytest.fixture
    def openstack_tfhelper(self):
        """Terraform helper for openstack tests."""
        return Mock()

    @pytest.fixture
    def openstack_jhelper(self):
        """Juju helper for openstack tests."""
        return Mock()

    @pytest.fixture
    def openstack_manifest(self):
        """Manifest for openstack tests."""
        manifest = Mock()
        manifest.core.config.pci = None
        return manifest

    def test_run(
        self,
        openstack_client,
        openstack_tfhelper,
        openstack_jhelper,
        openstack_manifest,
        openstack_read_config_patch,
    ):
        openstack_jhelper.get_application_names.return_value = [
            "placement",
            "nova-compute",
        ]
        step = ReapplyOpenStackTerraformPlanStep(
            openstack_client, openstack_tfhelper, openstack_jhelper, openstack_manifest
        )
        result = step.run()

        openstack_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self,
        openstack_client,
        openstack_tfhelper,
        openstack_jhelper,
        openstack_manifest,
        openstack_read_config_patch,
    ):
        openstack_tfhelper.update_tfvars_and_apply_tf.side_effect = TerraformException(
            "apply failed..."
        )

        step = ReapplyOpenStackTerraformPlanStep(
            openstack_client, openstack_tfhelper, openstack_jhelper, openstack_manifest
        )
        result = step.run()

        openstack_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        openstack_client,
        openstack_tfhelper,
        openstack_jhelper,
        openstack_manifest,
        openstack_read_config_patch,
    ):
        openstack_jhelper.get_application_names.return_value = [
            "placement",
            "nova-compute",
        ]
        openstack_jhelper.wait_until_active.side_effect = TimeoutError("timed out")

        step = ReapplyOpenStackTerraformPlanStep(
            openstack_client, openstack_tfhelper, openstack_jhelper, openstack_manifest
        )
        result = step.run()

        openstack_jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(
        self,
        openstack_client,
        openstack_tfhelper,
        openstack_jhelper,
        openstack_manifest,
        openstack_read_config_patch,
    ):
        openstack_jhelper.get_application_names.return_value = [
            "placement",
            "nova-compute",
        ]
        openstack_jhelper.wait_until_active.side_effect = JujuWaitException(
            "Unit in error: placement/0"
        )

        step = ReapplyOpenStackTerraformPlanStep(
            openstack_client, openstack_tfhelper, openstack_jhelper, openstack_manifest
        )
        result = step.run()

        openstack_jhelper.wait_until_active.assert_called_once()
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
