# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import Mock, patch

import pytest
import tenacity

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core import ovn
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
    EndpointsConfigurationStep,
    OpenStackPatchLoadBalancerServicesIPPoolStep,
    OpenStackPatchLoadBalancerServicesIPStep,
    ReapplyOpenStackTerraformPlanStep,
    compute_ha_scale,
    compute_ingress_scale,
    compute_os_api_scale,
    get_database_default_storage_dict,
    get_database_storage_dict,
    remove_blocked_apps_from_features,
    remove_blocked_apps_from_ovn_provider,
    remove_blocked_apps_from_role,
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
    storage_manager = Mock()
    storage_manager.list_principal_applications.return_value = []
    deployment.get_storage_manager.return_value = storage_manager
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
        deployment_with_client.get_ovn_manager().get_control_plane_tfvars.return_value = {}

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
        deployment_with_client.get_ovn_manager().get_control_plane_tfvars.return_value = {}
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
        deployment_with_client.get_ovn_manager().get_control_plane_tfvars.return_value = {}
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
        deployment_with_client.get_ovn_manager().get_control_plane_tfvars.return_value = {}
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


@pytest.fixture
def ovn_manager():
    """Ovn manager mock."""
    ovn_manager = Mock()
    ovn_manager.get_provider.return_value = ovn.OvnProvider.OVN_K8S
    yield ovn_manager


class PatchLoadBalancerServicesIPStepTest:
    @pytest.fixture
    def patch_client(self):
        """Client for patch tests."""
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = ["node-1"]
        return client

    def test_is_skip(
        self, patch_client, read_config_patch, snap_patch, snap_mock, ovn_manager
    ):
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
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_missing_annotation(
        self, patch_client, read_config_patch, snap_patch, snap_mock, ovn_manager
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
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_missing_config(
        self, patch_client, snap_patch, snap_mock, ovn_manager
    ):
        snap_mock().config.get.return_value = "k8s"
        with patch(
            "sunbeam.core.steps.read_config",
            new=Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run(
        self, patch_client, read_config_patch, snap_patch, snap_mock, ovn_manager
    ):
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
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            step.is_skip()
            result = step.run()
        assert result.result_type == ResultType.COMPLETED
        # Verify apply was called instead of patch
        step.kube.apply.assert_called_once()
        # Check that managedFields was cleared before apply
        service_arg = step.kube.apply.mock_calls[0][1][0]
        assert service_arg.metadata.annotations[METALLB_IP_ANNOTATION] == "fake-ip"
        # Verify field_manager was passed
        assert step.kube.apply.mock_calls[0][2]["field_manager"] == "sunbeam"


class TestPatchLoadBalancerServicesIPStaleAnnotation:
    """Tests for stale MetalLB IP annotation handling."""

    @pytest.fixture
    def patch_client(self):
        """Client mock; returns node-1 for any role so ovn-relay is excluded."""
        client = Mock()
        client.cluster.list_nodes_by_role.return_value = ["node-1"]
        return client

    def _make_service(self, ip_annotation=None, ingress_ip=None):
        """Helper: build a plausible lightkube Service mock."""
        annotations = {}
        if ip_annotation is not None:
            annotations[METALLB_IP_ANNOTATION] = ip_annotation
        ingress = [Mock(ip=ingress_ip)] if ingress_ip else None
        lb_status = Mock()
        lb_status.ingress = ingress
        status = Mock()
        status.loadBalancer = lb_status
        return Mock(
            metadata=Mock(
                annotations=annotations,
                name=None,
                managedFields=None,
            ),
            status=status,
        )

    def test_is_skip_stale_annotation_pending_returns_completed(
        self, patch_client, read_config_patch, snap_patch, snap_mock, ovn_manager
    ):
        """is_skip should return COMPLETED when a service has a stale IP annotation.

        Service is still in <pending> state (no allocated IP).
        """
        snap_mock().config.get.return_value = "k8s"

        # traefik and traefik-public have proper annotations+IPs; rabbitmq has a
        # stale annotation but no ingress (pending).
        svc_with_ip = self._make_service(ip_annotation="1.2.3.4", ingress_ip="1.2.3.4")
        svc_stale = self._make_service(ip_annotation="172.22.0.230", ingress_ip=None)

        get_mock = Mock(
            side_effect=[
                svc_with_ip,  # traefik-lb
                svc_with_ip,  # traefik-public-lb
                svc_stale,  # rabbitmq-lb  ← stale annotation, pending
            ]
        )
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run_removes_stale_ip_annotation(
        self, patch_client, read_config_patch, snap_patch, snap_mock, ovn_manager
    ):
        """run() should remove a stale IP annotation from a pending service.

        MetalLB can then assign a fresh IP from the pool.
        """
        snap_mock().config.get.return_value = "k8s"

        svc_with_ip = self._make_service(ip_annotation="1.2.3.4", ingress_ip="1.2.3.4")
        svc_with_ip.metadata.name = "traefik-lb"
        svc_stale = self._make_service(ip_annotation="172.22.0.230", ingress_ip=None)
        svc_stale.metadata.name = "rabbitmq-lb"

        # is_skip needs one pass over 3 services; run() needs another pass.
        get_mock = Mock(
            side_effect=[
                # is_skip pass
                svc_with_ip,  # traefik-lb
                svc_with_ip,  # traefik-public-lb
                svc_stale,  # rabbitmq-lb (stale → returns COMPLETED so run fires)
                # run pass
                svc_with_ip,  # traefik-lb  (annotation present + ingress → skip)
                svc_with_ip,  # traefik-public-lb  (same)
                svc_stale,  # rabbitmq-lb (stale → clear annotation)
                svc_with_ip,  # traefik-rgw-lb (annotation present + ingress → skip)
            ]
        )
        with patch(
            "sunbeam.core.steps.l_client.Client",
            new=Mock(return_value=Mock(get=get_mock)),
        ):
            step = OpenStackPatchLoadBalancerServicesIPStep(patch_client, ovn_manager)
            step.is_skip()
            result = step.run()

        assert result.result_type == ResultType.COMPLETED
        # patch must have been called exactly once — for rabbitmq-lb
        step.kube.patch.assert_called_once()
        call_args = step.kube.patch.mock_calls[0]
        # First positional arg is the resource type
        from lightkube.resources import core_v1

        assert call_args[1][0] is core_v1.Service
        # Second positional arg is the service name
        assert call_args[1][1] == "rabbitmq-lb"
        # Third positional arg is the patch body — annotation set to None (deletion)
        patch_body = call_args[1][2]
        assert patch_body["metadata"]["annotations"][METALLB_IP_ANNOTATION] is None
        # patch_type must be MERGE
        from lightkube.types import PatchType

        assert call_args[2]["patch_type"] == PatchType.MERGE


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
        # Verify apply was called instead of patch
        step.kube.apply.assert_called_once()
        # Check the annotation
        service_arg = step.kube.apply.mock_calls[0][1][0]
        assert (
            service_arg.metadata.annotations[METALLB_ADDRESS_POOL_ANNOTATION]
            == pool_name
        )
        # Verify field_manager was passed
        assert step.kube.apply.mock_calls[0][2]["field_manager"] == "sunbeam"

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
        # Verify apply was called instead of patch
        step.kube.apply.assert_called_once()
        # Check the annotation
        service_arg = step.kube.apply.mock_calls[0][1][0]
        assert (
            service_arg.metadata.annotations[METALLB_ADDRESS_POOL_ANNOTATION]
            == pool_name
        )
        # Verify field_manager was passed
        assert step.kube.apply.mock_calls[0][2]["field_manager"] == "sunbeam"

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
        step.kube.apply.assert_not_called()

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
        # Verify apply was called instead of patch
        step.kube.apply.assert_called_once()
        # Check the annotation
        service_arg = step.kube.apply.mock_calls[0][1][0]
        assert (
            service_arg.metadata.annotations[METALLB_ADDRESS_POOL_ANNOTATION]
            == pool_name
        )
        # Verify field_manager was passed
        assert step.kube.apply.mock_calls[0][2]["field_manager"] == "sunbeam"


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

    @pytest.fixture
    def openstack_deployment(self):
        """Deployment for openstack tests."""
        deployment = Mock()
        storage_manager = Mock()
        storage_manager.list_principal_applications.return_value = []
        deployment.get_storage_manager.return_value = storage_manager
        return deployment

    def test_run(
        self,
        openstack_deployment,
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
            openstack_deployment,
            openstack_client,
            openstack_tfhelper,
            openstack_jhelper,
            openstack_manifest,
            "test-machine-model",
        )
        result = step.run()

        openstack_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_tf_apply_failed(
        self,
        openstack_deployment,
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
            openstack_deployment,
            openstack_client,
            openstack_tfhelper,
            openstack_jhelper,
            openstack_manifest,
            "test-machine-model",
        )
        result = step.run()

        openstack_tfhelper.update_tfvars_and_apply_tf.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "apply failed..."

    def test_run_waiting_timed_out(
        self,
        openstack_deployment,
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
            openstack_deployment,
            openstack_client,
            openstack_tfhelper,
            openstack_jhelper,
            openstack_manifest,
            "test-machine-model",
        )
        result = step.run()

        openstack_jhelper.wait_until_active.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "timed out"

    def test_run_unit_in_error_state(
        self,
        openstack_deployment,
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
            openstack_deployment,
            openstack_client,
            openstack_tfhelper,
            openstack_jhelper,
            openstack_manifest,
            "test-machine-model",
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


# ---------------------------------------------------------------------------
# remove_blocked_apps_from_features
# ---------------------------------------------------------------------------


def test_remove_blocked_apps_from_features_active_app_excluded():
    jhelper = Mock()
    app_mock = Mock()
    app_mock.app_status.current = "active"
    jhelper.get_application.return_value = app_mock
    result = remove_blocked_apps_from_features(jhelper, "test-model")
    assert result == []


def test_remove_blocked_apps_from_features_blocked_app_included():
    jhelper = Mock()
    app_mock = Mock()
    app_mock.app_status.current = "blocked"
    jhelper.get_application.return_value = app_mock
    result = remove_blocked_apps_from_features(jhelper, "test-model")
    assert "barbican" in result
    assert "vault" in result


def test_remove_blocked_apps_from_features_missing_app_skipped():
    jhelper = Mock()
    jhelper.get_application.side_effect = ApplicationNotFoundException("not found")
    result = remove_blocked_apps_from_features(jhelper, "test-model")
    assert result == []


def test_remove_blocked_apps_from_features_mixed():
    jhelper = Mock()
    active_app = Mock()
    active_app.app_status.current = "active"
    blocked_app = Mock()
    blocked_app.app_status.current = "blocked"

    def _get_app(name, model):
        if name == "barbican":
            return blocked_app
        return active_app

    jhelper.get_application.side_effect = _get_app
    result = remove_blocked_apps_from_features(jhelper, "test-model")
    assert result == ["barbican"]


# ---------------------------------------------------------------------------
# remove_blocked_apps_from_ovn_provider
# ---------------------------------------------------------------------------


def test_remove_blocked_apps_from_ovn_provider_microovn():
    ovn_manager = Mock()
    ovn_manager.get_provider.return_value = ovn.OvnProvider.MICROOVN
    result = remove_blocked_apps_from_ovn_provider(ovn_manager)
    assert result == ["neutron"]


def test_remove_blocked_apps_from_ovn_provider_non_microovn():
    ovn_manager = Mock()
    ovn_manager.get_provider.return_value = ovn.OvnProvider.OVN_K8S
    result = remove_blocked_apps_from_ovn_provider(ovn_manager)
    assert result == []


# ---------------------------------------------------------------------------
# remove_blocked_apps_from_role
# ---------------------------------------------------------------------------


def test_remove_blocked_apps_from_role_no_special_role():
    result = remove_blocked_apps_from_role(
        external_keystone_model=None,
        is_region_controller=False,
    )
    assert result == []


def test_remove_blocked_apps_from_role_external_keystone():
    result = remove_blocked_apps_from_role(
        external_keystone_model="some-model",
        is_region_controller=False,
    )
    assert "keystone" in result
    assert "horizon" in result


def test_remove_blocked_apps_from_role_region_controller():
    result = remove_blocked_apps_from_role(
        external_keystone_model=None,
        is_region_controller=True,
    )
    assert "nova" in result
    assert "glance" in result
    assert "neutron" in result
    assert "placement" in result


def test_remove_blocked_apps_from_role_both():
    result = remove_blocked_apps_from_role(
        external_keystone_model="some-model",
        is_region_controller=True,
    )
    # external keystone group
    assert "keystone" in result
    assert "horizon" in result
    # region controller group
    assert "nova" in result
    assert "glance" in result


def _make_manifest_with_endpoints(endpoints_dict):
    """Build a real Manifest with the given endpoints config dict."""
    return Manifest.model_validate({"core": {"config": {"endpoints": endpoints_dict}}})


class TestEndpointsConfigurationStep:
    """Tests for the opt-in endpoint configuration prompt logic."""

    def _make_step(self, manifest=None):
        client = Mock()
        client.cluster.get_config.side_effect = lambda key: (
            '{"configure": false}'
            if key == ENDPOINTS_CONFIG_KEY
            else (_ for _ in ()).throw(ConfigItemNotFoundException(f"{key} not found"))
        )
        return EndpointsConfigurationStep(client, manifest=manifest), client

    # ------------------------------------------------------------------ #
    # Concrete subclass required because _validate_endpoint is abstract.  #
    # ------------------------------------------------------------------ #
    class _ConcreteStep(EndpointsConfigurationStep):
        def _validate_endpoint(self, endpoint, ip):
            return True

    def _make_concrete_step(self, manifest=None):
        client = Mock()
        client.cluster.get_config.side_effect = ConfigItemNotFoundException("not found")
        return self._ConcreteStep(client, manifest=manifest), client

    # ------------------------------------------------------------------ #
    # Backward-compatibility: no endpoints in manifest → skip silently    #
    # ------------------------------------------------------------------ #

    def test_no_manifest_skips_silently(self):
        """Without a manifest, prompt() should write configure=False and return."""
        step, client = self._make_concrete_step(manifest=None)
        step.prompt()

        client.cluster.update_config.assert_called_once_with(
            ENDPOINTS_CONFIG_KEY, '{"configure": false}'
        )

    def test_manifest_without_endpoints_skips_silently(self):
        """A manifest that has no endpoints section should skip silently."""
        manifest = Manifest.model_validate({})
        step, client = self._make_concrete_step(manifest=manifest)
        step.prompt()

        client.cluster.update_config.assert_called_once_with(
            ENDPOINTS_CONFIG_KEY, '{"configure": false}'
        )

    def test_manifest_endpoints_empty_object_skips_silently(self):
        """endpoints: {} (no keys set) should skip silently."""
        manifest = _make_manifest_with_endpoints({})
        step, client = self._make_concrete_step(manifest=manifest)
        step.prompt()

        client.cluster.update_config.assert_called_once_with(
            ENDPOINTS_CONFIG_KEY, '{"configure": false}'
        )

    # ------------------------------------------------------------------ #
    # Explicit configure: false → skip silently                           #
    # ------------------------------------------------------------------ #

    def test_manifest_configure_false_skips_silently(self):
        """configure: false must skip silently even if other keys are present."""
        manifest = _make_manifest_with_endpoints(
            {"configure": False, "ingress-internal": {"ip": "10.0.0.1"}}
        )
        step, client = self._make_concrete_step(manifest=manifest)
        step.prompt()

        client.cluster.update_config.assert_called_once_with(
            ENDPOINTS_CONFIG_KEY, '{"configure": false}'
        )

    # ------------------------------------------------------------------ #
    # Explicit configure: true → user is prompted (QuestionBank.ask)      #
    # ------------------------------------------------------------------ #

    def test_manifest_configure_true_asks_user(self):
        """configure: true without IP values should prompt the user."""
        manifest = _make_manifest_with_endpoints({"configure": True})
        step, client = self._make_concrete_step(manifest=manifest)

        with patch("sunbeam.steps.openstack.QuestionBank") as mock_qb_cls:
            mock_qb = Mock()
            mock_qb.configure.ask.return_value = False
            mock_qb_cls.return_value = mock_qb
            step.prompt()

        mock_qb.configure.ask.assert_called_once()

    # ------------------------------------------------------------------ #
    # Endpoint values present, no configure key → auto-configure          #
    # ------------------------------------------------------------------ #

    def test_manifest_with_ip_values_auto_configures(self):
        """Providing endpoint IP values without configure key should configure."""
        manifest = _make_manifest_with_endpoints(
            {"ingress-internal": {"ip": "10.0.0.1"}}
        )
        step, client = self._make_concrete_step(manifest=manifest)

        with patch("sunbeam.steps.openstack.QuestionBank") as mock_qb_cls:
            mock_qb = Mock()
            # Simulate user confirming configure=True (preseed forces it True)
            mock_qb.configure.ask.return_value = True
            mock_qb.configure_ip.ask.return_value = False
            mock_qb.configure_hostname.ask.return_value = False
            mock_qb_cls.return_value = mock_qb
            step.prompt()

        # preseed["configure"] is forced True → configure.ask returns True
        mock_qb.configure.ask.assert_called_once()
