# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import json
from unittest.mock import MagicMock, Mock, patch

import httpx
import lightkube
import lightkube.core.exceptions
import pytest
import tenacity
from lightkube import ApiError
from lightkube.types import PatchType

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Networks
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    LeaderNotFoundException,
    MachineNotFoundException,
)
from sunbeam.core.k8s import K8SError
from sunbeam.errors import SunbeamException
from sunbeam.steps.k8s import (
    CREDENTIAL_SUFFIX,
    K8S_CLOUD_SUFFIX,
    AddK8SCloudStep,
    AddK8SCredentialStep,
    DeployK8SApplicationStep,
    EnsureCiliumDeviceByHostStep,
    EnsureDefaultL2AdvertisementMutedStep,
    EnsureK8SUnitsTaggedStep,
    EnsureL2AdvertisementByHostStep,
    KubeClientError,
    PatchCoreDNSStep,
    PatchServiceExternalTrafficStep,
    StoreK8SKubeConfigStep,
    _get_machines_space_ips,
    get_kube_client,
)


# Common fixtures shared across test classes
@pytest.fixture
def named_deployment():
    """Deployment mock with name configured for tests that need it."""
    deployment = Mock()
    deployment.name = "test-deployment"
    return deployment


@pytest.fixture
def deployment_with_config():
    """Deployment mock with cluster config setup."""
    deployment = Mock()
    deployment.get_client().cluster.get_config.return_value = "{}"
    return deployment


@pytest.fixture
def deployment_with_space():
    """Deployment mock with space configuration."""
    deployment = Mock()
    deployment.name = "test-deployment"

    def get_space(network):
        if network == Networks.INTERNAL:
            return "internal"
        return "management"

    deployment.get_space.side_effect = get_space
    return deployment


@pytest.fixture
def client_with_config():
    """Client mock with cluster config setup."""
    return Mock(cluster=Mock(get_config=Mock(return_value="{}")))


@pytest.fixture
def jhelper_with_machines():
    """Jhelper mock with machine configuration for StoreK8SKubeConfigStep tests."""
    jhelper = Mock()
    mock_machine = MagicMock()
    mock_machine.addresses = [{"value": "127.0.0.1:16443", "space-name": "management"}]
    jhelper.get_machines.return_value = {"0": mock_machine}
    return jhelper


@pytest.fixture
def jhelper_with_networks():
    """Jhelper mock with network configuration for K8S units tests."""
    jhelper = Mock()
    jhelper.get_space_networks.return_value = [ipaddress.ip_network("10.0.0.0/8")]
    return jhelper


@pytest.fixture
def basic_kube():
    """Basic kube client mock."""
    return Mock()


@pytest.fixture
def basic_kubeconfig():
    """Basic kubeconfig mock."""
    return Mock()


@pytest.fixture
def test_service_name():
    """Standard test service name."""
    return "test-service"


class TestAddK8SCloudStep:
    @pytest.fixture
    def deployment(self, deployment_with_config):
        return deployment_with_config

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def cloud_name(self, deployment):
        return f"{deployment.name}{K8S_CLOUD_SUFFIX}"

    @pytest.fixture
    def setup_deployment(self, deployment):
        deployment.get_client().cluster.get_config.return_value = "{}"
        return deployment

    def test_is_skip(self, setup_deployment, jhelper, cloud_name, step_context):
        clouds = {}
        jhelper.get_clouds.return_value = clouds

        step = AddK8SCloudStep(setup_deployment, jhelper)
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_cloud_already_deployed(
        self,
        setup_deployment,
        jhelper,
        cloud_name,
        step_context,
    ):
        clouds = {cloud_name: {"endpoint": "10.0.10.1"}}
        jhelper.get_clouds.return_value = clouds

        step = AddK8SCloudStep(setup_deployment, jhelper)
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, setup_deployment, jhelper, cloud_name, step_context):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCloudStep(setup_deployment, jhelper)
            result = step.run(step_context)

        jhelper.add_k8s_cloud.assert_called_with(
            cloud_name,
            f"{cloud_name}{CREDENTIAL_SUFFIX}",
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestAddK8SCredentialStep:
    @pytest.fixture
    def deployment(self, deployment_with_config):
        deployment_with_config.name = "mydeployment"
        return deployment_with_config

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def cloud_name(self, deployment):
        return f"{deployment.name}{K8S_CLOUD_SUFFIX}"

    @pytest.fixture
    def credential_name(self, cloud_name):
        return f"{cloud_name}{CREDENTIAL_SUFFIX}"

    def test_is_skip(self, deployment, jhelper, step_context):
        credentials = {}
        jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(deployment, jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_credential_exists(
        self, deployment, jhelper, credential_name, step_context
    ):
        credentials = {"controller-credentials": {credential_name: {}}}
        jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(deployment, jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, deployment, jhelper, cloud_name, credential_name, step_context):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCredentialStep(deployment, jhelper)
            result = step.run(step_context)

        jhelper.add_k8s_credential.assert_called_with(
            cloud_name,
            credential_name,
            {},
        )
        assert result.result_type == ResultType.COMPLETED


class TestStoreK8SKubeConfigStep:
    @pytest.fixture
    def client(self, client_with_config):
        return client_with_config

    @pytest.fixture
    def jhelper(self, jhelper_with_machines):
        return jhelper_with_machines

    @pytest.fixture
    def deployment(self, deployment_with_space):
        return deployment_with_space

    def test_is_skip(self, deployment, client, jhelper, step_context):
        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.is_skip(step_context)

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_config_missing(self, deployment, client, jhelper, step_context):
        with patch(
            "sunbeam.steps.k8s.read_config",
            Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, deployment, client, jhelper, step_context):
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
        jhelper.run_action.return_value = action_result
        jhelper.get_leader_unit_machine.return_value = "0"
        jhelper.get_space_networks.return_value = {}
        jhelper.get_machine_interfaces.return_value = {
            "enp0s8": Mock(
                ip_addresses=["127.0.0.1"],
                space="management",
            )
        }

        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, deployment, client, jhelper, step_context):
        jhelper.get_leader_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_leader_not_found(self, deployment, client, jhelper, step_context):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )

        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Leader missing..."

    def test_run_action_failed(self, deployment, client, jhelper, step_context):
        jhelper.run_action.side_effect = ActionFailedException("Action failed...")
        jhelper.get_leader_unit.return_value = "k8s/0"
        jhelper.get_leader_unit_machine.return_value = "0"
        jhelper.get_space_networks.return_value = {}
        jhelper.get_machine_interfaces.return_value = {
            "enp0s8": Mock(
                ip_addresses=["127.0.0.1"],
                space="management",
            )
        }
        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run(step_context)

        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."


class TestEnsureL2AdvertisementByHostStep:
    @pytest.fixture
    def deployment(self, basic_deployment):
        return basic_deployment

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def control_nodes(self):
        return [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]

    @pytest.fixture
    def client(self, control_nodes):
        return Mock(
            cluster=Mock(
                list_nodes_by_role=Mock(return_value=control_nodes),
                get_config=Mock(return_value="{}"),
            )
        )

    @pytest.fixture
    def step(self, deployment, client, jhelper):
        model = "test-model"
        network = Mock()
        pool = "test-pool"
        step = EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            model,
            network,
            pool,
        )
        step.kube = Mock()
        step.kubeconfig = Mock()
        return step

    @pytest.fixture(autouse=True)
    def setup_patches(self, step):
        kubeconfig_mocker = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=step.kubeconfig)),
        )
        kubeconfig_mocker.start()

        kube_mocker = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=Mock(return_value=step.kube)),
        )
        kube_mocker.start()

        yield

        kubeconfig_mocker.stop()
        kube_mocker.stop()

    def test_is_skip_no_outdated_or_deleted(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=([], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_outdated(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=(["node1"], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1

    def test_is_skip_with_deleted(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=([], ["node2"]))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_delete) == 1

    def test_is_skip_single_node_fqdn_preserves_other_resources(
        self, deployment, client, jhelper, step_context
    ):
        """When fqdn is set, resources for other nodes must not be deleted.

        Regression test: during greenfield join, only the joining node is in
        the working set.  _get_outdated_resources marks other nodes' resources
        as deleted, but the base class must suppress deletions in single-node
        mode so previously-joined nodes keep their L2Advertisement.
        """
        node_info = {"name": "node2", "machineid": "2", "role": ["control"]}
        client.cluster.get_node_info = Mock(return_value=node_info)
        network = Mock()
        step = EnsureL2AdvertisementByHostStep(
            deployment,
            client,
            jhelper,
            "test-model",
            network,
            "test-pool",
            fqdn="node2.maas",
        )
        step.kube = Mock()
        step._get_outdated_resources = Mock(return_value=(["node2"], ["node1"]))
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1
        assert step.to_update[0]["name"] == "node2"
        assert step.to_delete == []

    def test_run_update_and_delete(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = [{"name": "node2", "machineid": "2"}]
        step._get_interface = Mock(return_value="eth0")
        step.kube.apply = Mock()
        step.kube.delete = Mock()

        result = step.run(None)

        step.kube.apply.assert_called_once()
        step.kube.delete.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_update_failure(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.apply = Mock(side_effect=api_error)

        result = step.run(None)

        step.kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED

    def test_run_missing_machine_skips(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(
            side_effect=MachineNotFoundException("Machine not found")
        )

        result = step.run(None)

        assert result.result_type == ResultType.SKIPPED
        assert (
            "Failed to get machines for L2 advertisement on nodes: node1"
            in result.message
        )

    def test_run_delete_failure(self, step):
        step.to_update = []
        step.to_delete = [{"name": "node2", "machineid": "2"}]
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.delete = Mock(side_effect=api_error)

        result = step.run(None)

        step.kube.delete.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_get_interface_cached(self, step):
        step._ifnames = {"node1": "eth0"}
        result = step._get_interface({"name": "node1"})
        assert result == "eth0"

    def test_get_interface_found(self, step, jhelper, deployment):
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="management"),
            "eth1": Mock(space="other-space"),
        }
        deployment.get_space.return_value = "management"
        result = step._get_interface({"name": "node1", "machineid": "1"})
        assert result == "eth0"
        assert step._ifnames["node1"] == "eth0"

    def test_get_interface_not_found(self, step, jhelper, deployment):
        """Test that _get_interface raises exception when interface is not found."""
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="other-space"),
            "eth1": Mock(space="another-space"),
        }
        deployment.get_space.return_value = "management"
        step.network = Mock()
        step.network.name = "test-network"

        # Test the private method directly - it should raise an exception
        # Using a standard exception pattern instead of accessing the
        # private exception class
        with pytest.raises(Exception) as exc_info:
            step._get_interface({"name": "node1", "machineid": "1"})

        # Verify it's the expected error message
        assert "Node node1 has no interface in test-network space" in str(
            exc_info.value
        )

    def test_ensure_l2_advertisement_retry(self, step):
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=500,
                content=json.dumps(
                    {
                        "code": 500,
                        "reason": 'Internal error occurred: failed calling webhook "l2advertisementvalidationwebhook.metallb.io"',
                    }
                ),
            ),
        )
        step.kube.apply.side_effect = [api_error, None]
        step._ensure_l2_advertisement.retry.wait = tenacity.wait_none()
        step._ensure_l2_advertisement("node1", "eth0")


def _to_kube_object(
    metadata: dict, spec: dict | None = None, status: dict | None = None
) -> object:
    """Convert a dictionary to a mock object."""
    obj = Mock()
    obj.metadata = Mock(**metadata)
    if "name" in metadata:
        obj.metadata.name = metadata["name"]
    obj.spec = spec
    if status:
        obj.status = Mock(**status)
    return obj


_l2_outdated_testcases = {
    "1-node-no-l2": ([{"name": "node1", "interface": "eth0"}], [], ["node1"], []),
    "1-node-matching-l2": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth0"]},
            )
        ],
        [],
        [],
    ),
    "1-node-wrong-pool-l2": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec={"ipAddressPools": ["my-pool"], "interfaces": ["eth0"]},
            )
        ],
        ["node1"],
        [],
    ),
    "1-node-wrong-interface-l2": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth1"]},
            )
        ],
        ["node1"],
        [],
    ),
    "0-node-l2-advertisement": (
        [],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth0"]},
            )
        ],
        [],
        ["node1"],
    ),
    "2-nodes-1-missing-l2-1-outdated-l2-1-l2-to-delete": (
        [
            {"name": "node2", "interface": "2"},
            {"name": "node3", "interface": "3"},
        ],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth0"]},
            ),
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node2"}},
                spec={"ipAddressPools": ["my-pool"], "interfaces": ["eth1"]},
            ),
        ],
        ["node2", "node3"],
        ["node1"],
    ),
    "missing-metadata": (
        [{"name": "node1", "interface": "eth0"}],
        [Mock(metadata=None)],
        ["node1"],
        [],
    ),
    "missing-labels": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": None},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth0"]},
            )
        ],
        ["node1"],
        [],
    ),
    "missing-hostname-in-labels": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": {}},
                spec={"ipAddressPools": ["test-pool"], "interfaces": ["eth0"]},
            )
        ],
        ["node1"],
        [],
    ),
    "missing-spec": (
        [{"name": "node1", "interface": "eth0"}],
        [
            _to_kube_object(
                metadata={"labels": {"sunbeam/hostname": "node1"}},
                spec=None,
            )
        ],
        ["node1"],
        [],
    ),
}


@pytest.mark.parametrize(
    "nodes,list,outdated,deleted",
    _l2_outdated_testcases.values(),
    ids=_l2_outdated_testcases.keys(),
)
def test_get_outdated_l2_advertisement(
    nodes: list[dict], list: list[object], outdated: list[str], deleted: list[str]
):
    kube = Mock(list=Mock(return_value=list))
    step = EnsureL2AdvertisementByHostStep(
        Mock(),
        Mock(),
        Mock(),
        "test-model",
        Mock(),
        "test-pool",
    )

    def _get_interface(node):
        for node_it in nodes:
            if node_it["name"] == node["name"]:
                return node_it["interface"]
        raise RuntimeError(f"Node {node['name']} has no interface in network space")

    step._get_interface = Mock(side_effect=_get_interface)

    outdated_res, deleted_res = step._get_outdated_resources(nodes, kube)

    assert outdated_res == outdated
    assert deleted_res == deleted


class TestEnsureDefaultL2AdvertisementMutedStep:
    @pytest.fixture
    def deployment(self, named_deployment):
        return named_deployment

    @pytest.fixture
    def client(self, basic_client):
        return basic_client

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def kubeconfig(self, basic_kubeconfig):
        return basic_kubeconfig

    @pytest.fixture
    def kube(self, basic_kube):
        return basic_kube

    @pytest.fixture
    def l2_advertisement_resource(self):
        return Mock()

    @pytest.fixture
    def l2_advertisement_namespace(self):
        return "test-namespace"

    @pytest.fixture
    def default_l2_advertisement(self):
        return "default-pool"

    @pytest.fixture
    def node_selectors(self):
        return [
            {
                "matchLabels": {
                    "kubernetes.io/hostname": "not-existing.sunbeam",
                }
            }
        ]

    @pytest.fixture(autouse=True)
    def setup_patches(
        self,
        kubeconfig,
        kube,
        l2_advertisement_resource,
        l2_advertisement_namespace,
        default_l2_advertisement,
    ):
        # Patch K8SHelper static methods
        k8shelper_patch = patch.multiple(
            "sunbeam.steps.k8s.K8SHelper",
            get_lightkube_l2_advertisement_resource=Mock(
                return_value=l2_advertisement_resource
            ),
            get_loadbalancer_namespace=Mock(return_value=l2_advertisement_namespace),
            get_internal_pool_name=Mock(return_value=default_l2_advertisement),
            get_kubeconfig_key=Mock(return_value="kubeconfig-key"),
        )
        k8shelper_patch.start()

        # Patch l_kubeconfig and l_client
        kubeconfig_patch = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=kubeconfig)),
        )
        kubeconfig_patch.start()

        kube_patch = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=kube),
        )
        kube_patch.start()

        # Patch meta_v1
        meta_v1_patch = patch(
            "sunbeam.steps.k8s.meta_v1.ObjectMeta",
            Mock(return_value=Mock()),
        )
        meta_v1_patch.start()

        yield

        k8shelper_patch.stop()
        kubeconfig_patch.stop()
        kube_patch.stop()
        meta_v1_patch.stop()

    def test_is_skip_kubeconfig_not_found(
        self, deployment, client, jhelper, step_context
    ):
        with patch(
            "sunbeam.steps.k8s.read_config", side_effect=ConfigItemNotFoundException
        ):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "kubeconfig not found" in result.message

    def test_is_skip_l2_advertisement_not_found(
        self,
        deployment,
        client,
        jhelper,
        kube,
        step_context,
    ):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=404)
        kube.get = Mock(side_effect=api_error)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_api_error_other(
        self,
        deployment,
        client,
        jhelper,
        kube,
        step_context,
    ):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(side_effect=api_error)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_l2_advertisement_already_muted(
        self,
        deployment,
        client,
        jhelper,
        kube,
        node_selectors,
        step_context,
    ):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": node_selectors}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_needs_muting(
        self,
        deployment,
        client,
        jhelper,
        kube,
        step_context,
    ):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": [{"matchLabels": {"foo": "bar"}}]}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_run_success(self, deployment, client, jhelper, kube):
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            step.kube = kube
            kube.apply = Mock(return_value=None)
            result = step.run(None)
        kube.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_api_error(self, deployment, client, jhelper, kube):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            step.kube = kube
            kube.apply = Mock(side_effect=api_error)
            result = step.run(None)
        kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert "Failed to update L2 default advertisement" in result.message


class TestEnsureK8SUnitsTaggedStep:
    @pytest.fixture
    def deployment(self, deployment_with_space):
        return deployment_with_space

    @pytest.fixture
    def client(self, basic_client):
        return basic_client

    @pytest.fixture
    def jhelper(self, jhelper_with_networks):
        return jhelper_with_networks

    @pytest.fixture
    def step(self, deployment, client, jhelper):
        model = "test-model"
        step = EnsureK8SUnitsTaggedStep(deployment, client, jhelper, model)
        kube = Mock()
        step.kube = kube
        return step

    def test_is_skip_no_nodes_to_update(self, step, client, jhelper, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        client.cluster.list_nodes_by_role.return_value = control_nodes
        step.kube.list.return_value = [
            _to_kube_object(
                {"name": "node1", "labels": {"sunbeam/hostname": "node1"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.1")]},
            ),
            _to_kube_object(
                {"name": "node2", "labels": {"sunbeam/hostname": "node2"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.2")]},
            ),
        ]
        jhelper.get_machines.return_value = {
            "1": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_nodes_to_update(self, step, client, jhelper, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        client.cluster.list_nodes_by_role.return_value = control_nodes
        step.kube.list.return_value = [
            _to_kube_object(
                {"name": "node1", "labels": {"sunbeam/hostname": "node1"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.1")]},
            ),
            _to_kube_object(
                {"name": "node2", "labels": {}},  # Missing label
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.2")]},
            ),
        ]
        jhelper.get_machines.return_value = {
            "1": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert "node2" in step.to_update

    def test_is_skip_nodes_to_update_with_fqdn(
        self, step, client, jhelper, step_context
    ):
        control_nodes = [
            {"name": "node1.maas", "machineid": "1"},
            {"name": "node2.maas", "machineid": "2"},
        ]
        client.cluster.list_nodes_by_role.return_value = control_nodes
        step.kube.list.return_value = [
            _to_kube_object(
                {"name": "node1", "labels": {"sunbeam/hostname": "node1"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.1")]},
            ),
            _to_kube_object(
                {"name": "node2", "labels": {}},  # Missing label
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.2")]},
            ),
        ]
        jhelper.get_machines.return_value = {
            "1": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert "node2.maas" in step.to_update

    def test_is_skip_kube_client_error(self, step, client, step_context):
        client.cluster.list_nodes_by_role.return_value = []
        with patch(
            "sunbeam.steps.k8s.get_kube_client", side_effect=KubeClientError("fail")
        ):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_k8s_api_error(self, step, client, jhelper, step_context):
        client.cluster.list_nodes_by_role.return_value = [
            {"name": "node1", "machineid": "1"}
        ]
        jhelper.get_machines.return_value = {
            "1": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="internal", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.list.side_effect = api_error
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_missing(self, step, client, jhelper, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
        ]
        client.cluster.list_nodes_by_role.return_value = control_nodes
        step.kube.list.return_value = [Mock(metadata=Mock(name="node1", labels={}))]
        jhelper.get_machines.return_value = {}
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_not_control_role(self, step, client, step_context):
        step.fqdn = "node1"
        client.cluster.get_node_info.return_value = {
            "name": "node1",
            "machineid": "1",
            "role": "compute",
        }
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_success(self, step):
        step.to_update = {"node1": "k8s-node1"}
        step.kube.apply = Mock()
        with (
            patch("sunbeam.steps.k8s.core_v1.Node", Mock()),
            patch("sunbeam.steps.k8s.meta_v1.ObjectMeta", Mock()),
        ):
            result = step.run(None)
        step.kube.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_apply_failure(self, step):
        step.to_update = {"node1": "k8s-node1"}
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.apply = Mock(side_effect=api_error)
        with (
            patch("sunbeam.steps.k8s.core_v1.Node", Mock()),
            patch("sunbeam.steps.k8s.meta_v1.ObjectMeta", Mock()),
        ):
            result = step.run(None)
        step.kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED


class TestDeployK8SApplicationStep:
    @pytest.fixture
    def deployment(self, deployment_with_space):
        deployment_with_space.openstack_machines_model = "test-model"
        return deployment_with_space

    @pytest.fixture
    def manifest(self, basic_manifest):
        basic_manifest.core.software.charms.get.return_value = None
        return basic_manifest

    @pytest.fixture
    def step(self, deployment, basic_client, basic_tfhelper, basic_jhelper, manifest):
        basic_client.cluster.get_config.return_value = "{}"
        return DeployK8SApplicationStep(
            deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            manifest,
            "test-model",
        )

    def test_extra_tfvars_binds_cluster_endpoint_to_internal_space(self, step):
        assert step.extra_tfvars()["endpoint_bindings"] == [
            {"space": "management"},
            {"endpoint": "cluster", "space": "internal"},
        ]

    def test_get_k8s_config_tfvars_does_not_manage_cluster_annotations(self, step):
        assert "cluster-annotations" not in step._get_k8s_config_tfvars()

    def test_get_k8s_config_tfvars_sets_default_toleration_seconds(self, step):
        config = step._get_k8s_config_tfvars()
        apiserver_args = config.get("kube-apiserver-extra-args", "")
        assert "default-not-ready-toleration-seconds=60" in apiserver_args
        assert "default-unreachable-toleration-seconds=60" in apiserver_args

    def test_get_k8s_config_tfvars_merges_toleration_seconds_with_existing_args(
        self, step, manifest
    ):
        charm_mock = Mock()
        charm_mock.config = {"kube-apiserver-extra-args": "xyz-flag=true"}
        manifest.core.software.charms.get.return_value = charm_mock
        config = step._get_k8s_config_tfvars()
        apiserver_args = config.get("kube-apiserver-extra-args", "")
        assert "xyz-flag=true" in apiserver_args
        assert "default-not-ready-toleration-seconds=60" in apiserver_args
        assert "default-unreachable-toleration-seconds=60" in apiserver_args


class TestGetKubeClient:
    @pytest.fixture
    def client(self, basic_client):
        return basic_client

    @pytest.fixture
    def namespace(self, test_namespace):
        return test_namespace

    @patch("sunbeam.steps.k8s.read_config")
    @patch(
        "sunbeam.steps.k8s.K8SHelper.get_kubeconfig_key", return_value="kubeconfig-key"
    )
    @patch("sunbeam.steps.k8s.l_kubeconfig.KubeConfig.from_dict")
    @patch("sunbeam.steps.k8s.l_client.Client")
    def test_get_kube_client_success(
        self,
        mock_client,
        mock_kubeconfig_from_dict,
        mock_get_kubeconfig_key,
        mock_read_config,
        client,
        namespace,
    ):
        mock_read_config.return_value = {"apiVersion": "v1"}
        mock_kubeconfig_from_dict.return_value = Mock()

        result = get_kube_client(client, namespace)

        mock_read_config.assert_called_once_with(client, "kubeconfig-key")
        mock_kubeconfig_from_dict.assert_called_once_with({"apiVersion": "v1"})
        mock_client.assert_called_once_with(
            mock_kubeconfig_from_dict.return_value,
            namespace,
            trust_env=False,
        )
        assert result == mock_client.return_value

    @patch("sunbeam.steps.k8s.read_config", side_effect=ConfigItemNotFoundException)
    @patch(
        "sunbeam.steps.k8s.K8SHelper.get_kubeconfig_key", return_value="kubeconfig-key"
    )
    def test_get_kube_client_config_not_found(
        self, mock_get_kubeconfig_key, mock_read_config, client, namespace
    ):
        with pytest.raises(KubeClientError) as context:
            get_kube_client(client, namespace)

        mock_read_config.assert_called_once_with(client, "kubeconfig-key")
        assert "K8S kubeconfig not found" in str(context.value)

    @patch("sunbeam.steps.k8s.read_config")
    @patch(
        "sunbeam.steps.k8s.K8SHelper.get_kubeconfig_key", return_value="kubeconfig-key"
    )
    @patch("sunbeam.steps.k8s.l_kubeconfig.KubeConfig.from_dict")
    @patch(
        "sunbeam.steps.k8s.l_client.Client",
        side_effect=lightkube.core.exceptions.ConfigError,
    )
    def test_get_kube_client_config_error(
        self,
        mock_client,
        mock_kubeconfig_from_dict,
        mock_get_kubeconfig_key,
        mock_read_config,
        client,
        namespace,
    ):
        mock_read_config.return_value = {"apiVersion": "v1"}
        mock_kubeconfig_from_dict.return_value = Mock()

        with pytest.raises(KubeClientError) as context:
            get_kube_client(client, namespace)

        mock_read_config.assert_called_once_with(client, "kubeconfig-key")
        mock_kubeconfig_from_dict.assert_called_once_with({"apiVersion": "v1"})
        mock_client.assert_called_once_with(
            mock_kubeconfig_from_dict.return_value,
            namespace,
            trust_env=False,
        )
        assert "Error creating k8s client" in str(context.value)


_get_machines_space_ips_tests_cases = {
    "match_ip_in_space_and_network": (
        {
            "eth0": Mock(space="mgmt", ip_addresses=["10.0.0.5", "192.168.1.2"]),
            "eth1": Mock(space="data", ip_addresses=["172.16.0.1"]),
        },
        "mgmt",
        [ipaddress.ip_network("10.0.0.0/24"), ipaddress.ip_network("192.168.1.0/24")],
        {"10.0.0.5", "192.168.1.2"},
    ),
    "no_matching_space": (
        {"eth0": Mock(space="data", ip_addresses=["10.0.0.5"])},
        "mgmt",
        [ipaddress.ip_network("10.0.0.0/24")],
        set(),
    ),
    "no_matching_network": (
        {"eth0": Mock(space="mgmt", ip_addresses=["172.16.0.1"])},
        "mgmt",
        [ipaddress.ip_network("10.0.0.0/24")],
        set(),
    ),
    "invalid_ip_address": (
        {"eth0": Mock(space="mgmt", ip_addresses=["not-an-ip", "10.0.0.5"])},
        "mgmt",
        [ipaddress.ip_network("10.0.0.0/24")],
        {"10.0.0.5"},
    ),
    "multiple_interfaces_and_networks": (
        {
            "eth0": Mock(space="mgmt", ip_addresses=["10.0.0.5", "192.168.1.2"]),
            "eth1": Mock(space="mgmt", ip_addresses=["172.16.0.1", "10.0.0.6"]),
            "eth2": Mock(space="data", ip_addresses=["10.1.0.1"]),
        },
        "mgmt",
        [ipaddress.ip_network("10.0.0.0/24"), ipaddress.ip_network("172.16.0.0/16")],
        {"10.0.0.5", "10.0.0.6", "172.16.0.1"},
    ),
}


@pytest.mark.parametrize(
    "interfaces,space,networks,expected",
    _get_machines_space_ips_tests_cases.values(),
    ids=_get_machines_space_ips_tests_cases.keys(),
)
def test_get_machines_space_ips(interfaces, space, networks, expected):
    result = set(_get_machines_space_ips(interfaces, space, networks))
    assert result == expected


class TestPatchCoreDNSStep:
    @pytest.fixture
    def deployment(self, basic_deployment):
        return basic_deployment

    @pytest.fixture
    def client(self, basic_client):
        return basic_client

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def step(self, deployment, jhelper):
        return PatchCoreDNSStep(deployment, jhelper)

    @pytest.fixture
    def kube(self, basic_kube):
        return basic_kube

    def test_is_skip(self, step, kube, step_context):
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=404,
                content=json.dumps(
                    {
                        "code": 404,
                        "message": "horizontal podautoscaler not found",
                    }
                ),
            ),
        )
        kube.get = Mock(side_effect=api_error)

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_no_hpa_computes_replica_count(self, step, kube, step_context):
        """Replica count must be derived from control-node count even without HPA.

        Previously replica_count defaulted to 1 because the computation was inside
        the try-block that raised on 404.
        """
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
            {"name": "node3", "machineid": "3"},
        ]
        step.client.cluster.list_nodes_by_role.return_value = control_nodes
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=404,
                content=json.dumps(
                    {
                        "code": 404,
                        "message": "horizontalpodautoscalers.autoscaling"
                        ' "ck-dns-coredns" not found',
                    }
                ),
            ),
        )
        kube.get = Mock(side_effect=api_error)

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            result = step.is_skip(step_context)

        assert result.result_type == ResultType.COMPLETED
        # 3 control nodes → replica_count must be 3, not the default 1
        assert step.replica_count == 3

    def test_is_skip_kube_get_error(self, step, kube, step_context):
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=500,
                content=json.dumps(
                    {
                        "code": 500,
                        "message": "Unknown error",
                    }
                ),
            ),
        )
        kube.get = Mock(side_effect=api_error)

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_hpa_already_exists(self, step, kube, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 1

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            kube.get = Mock(return_value=hpa)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_new_control_nodes_added(self, step, kube, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
            {"name": "node3", "machineid": "3"},
        ]
        step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 1

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            kube.get = Mock(return_value=hpa)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.replica_count == 3

    def test_is_skip_control_nodes_removed(self, step, kube, step_context):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 3

        with patch("sunbeam.steps.k8s.get_kube_client", return_value=kube):
            kube.get = Mock(return_value=hpa)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.replica_count == 1

    def test_run(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        with patch.object(step, "_wait_for_coredns_ready"):
            result = step.run(None)
        assert result.result_type == ResultType.COMPLETED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_helm_upgrade_failed(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=1)
        with patch.object(step, "_wait_for_coredns_ready") as mock_wait:
            result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()
        mock_wait.assert_not_called()

    def test_run_failed_on_juju_run_on_machine_unit(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.side_effect = JujuException(
            "Not able to run command"
        )
        with patch.object(step, "_wait_for_coredns_ready") as mock_wait:
            result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()
        mock_wait.assert_not_called()

    def test_run_leader_not_found(self, step, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )
        with patch.object(step, "_wait_for_coredns_ready") as mock_wait:
            result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_not_called()
        mock_wait.assert_not_called()

    def test_run_waits_for_coredns_ready(self, step, jhelper):
        """_wait_for_coredns_ready must be called after a successful helm upgrade.

        Ensures DNS is available before subsequent OpenStack deployment steps.
        """
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        with patch.object(step, "_wait_for_coredns_ready") as mock_wait:
            result = step.run(None)
        assert result.result_type == ResultType.COMPLETED
        mock_wait.assert_called_once()

    def test_run_coredns_ready_timeout(self, step, jhelper):
        """A TimeoutError from _wait_for_coredns_ready must propagate as FAILED.

        Ensures the deployment does not silently proceed with DNS unavailable.
        """
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        with patch.object(
            step,
            "_wait_for_coredns_ready",
            side_effect=TimeoutError("timed out"),
        ):
            result = step.run(None)
        assert result.result_type == ResultType.FAILED

    def test_run_coredns_deployment_not_found(self, step, jhelper):
        """A K8SError from _wait_for_coredns_ready must propagate as FAILED."""
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        with patch.object(
            step,
            "_wait_for_coredns_ready",
            side_effect=K8SError("CoreDNS deployment 'ck-dns-coredns' not found"),
        ):
            result = step.run(None)
        assert result.result_type == ResultType.FAILED

    def test_wait_for_coredns_ready_when_already_ready(self, step, kube):
        """Returns immediately when availableReplicas >= replica_count."""
        step.kube = kube
        step.replica_count = 2
        deployment = Mock()
        deployment.status = Mock()
        deployment.status.availableReplicas = 2
        kube.get = Mock(return_value=deployment)
        step._wait_for_coredns_ready()  # must not raise
        kube.get.assert_called_once()

    def test_wait_for_coredns_ready_polls_until_ready(self, step, kube):
        """Polls until availableReplicas reaches replica_count."""
        step.kube = kube
        step.replica_count = 1
        not_ready = Mock()
        not_ready.status = Mock()
        not_ready.status.availableReplicas = 0
        ready = Mock()
        ready.status = Mock()
        ready.status.availableReplicas = 1
        kube.get = Mock(side_effect=[not_ready, ready])
        with patch("sunbeam.steps.k8s.time.sleep"):
            step._wait_for_coredns_ready()
        assert kube.get.call_count == 2

    def test_wait_for_coredns_ready_retries_on_5xx_api_error(self, step, kube):
        """Retries only on transient 5xx errors."""
        step.kube = kube
        step.replica_count = 1
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=503,
                content=json.dumps({"code": 503, "message": "service unavailable"}),
            ),
        )
        ready = Mock()
        ready.status = Mock()
        ready.status.availableReplicas = 1
        kube.get = Mock(side_effect=[api_error, ready])
        with patch("sunbeam.steps.k8s.time.sleep"):
            step._wait_for_coredns_ready()
        assert kube.get.call_count == 2

    def test_wait_for_coredns_ready_fails_fast_on_404(self, step, kube):
        """Raises K8SError immediately when deployment is not found (404)."""
        step.kube = kube
        step.replica_count = 1
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=404,
                content=json.dumps({"code": 404, "message": "not found"}),
            ),
        )
        kube.get = Mock(side_effect=api_error)
        with pytest.raises(K8SError, match="not found"):
            step._wait_for_coredns_ready()
        kube.get.assert_called_once()

    def test_wait_for_coredns_ready_reraises_non_transient_api_error(self, step, kube):
        """Re-raises ApiError for non-404, non-5xx status codes."""
        step.kube = kube
        step.replica_count = 1
        api_error = ApiError(
            Mock(),
            httpx.Response(
                status_code=403,
                content=json.dumps({"code": 403, "message": "forbidden"}),
            ),
        )
        kube.get = Mock(side_effect=api_error)
        with pytest.raises(ApiError):
            step._wait_for_coredns_ready()
        kube.get.assert_called_once()

    def test_wait_for_coredns_ready_timeout(self, step, kube):
        """Raises TimeoutError when pods do not become ready in time."""
        step.kube = kube
        step.replica_count = 3
        deployment = Mock()
        deployment.status = Mock()
        deployment.status.availableReplicas = 0
        kube.get = Mock(return_value=deployment)
        with (
            patch("sunbeam.steps.k8s.K8S_APP_TIMEOUT", 0),
            patch("sunbeam.steps.k8s.time.sleep"),
        ):
            with pytest.raises(TimeoutError):
                step._wait_for_coredns_ready()


class TestPatchServiceExternalTrafficStep:
    @pytest.fixture
    def deployment(self, basic_deployment):
        deployment = basic_deployment
        deployment.get_client.return_value = Mock()
        return deployment

    @pytest.fixture
    def client(self, deployment):
        return deployment.get_client()

    @pytest.fixture
    def service_name(self, test_service_name):
        return test_service_name

    @pytest.fixture
    def namespace(self, test_namespace):
        return test_namespace

    @pytest.fixture
    def step(self, deployment, service_name, namespace):
        step = PatchServiceExternalTrafficStep(deployment, service_name, namespace)
        kube = Mock()
        step.kube = kube
        return step

    def test_is_skip_external_traffic_policy_already_local(
        self, step, service_name, step_context
    ):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Local"
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_external_traffic_policy_not_local(
        self, step, service_name, step_context
    ):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Cluster"
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_service_has_no_spec(self, step, service_name, step_context):
        service = Mock()
        service.spec = None
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_kube_client_error(self, step, step_context):
        with patch(
            "sunbeam.steps.k8s.get_kube_client", side_effect=KubeClientError("fail")
        ):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_is_skip_api_error(self, step, step_context):
        api_error = lightkube.core.exceptions.ApiError.__new__(
            lightkube.core.exceptions.ApiError
        )
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.side_effect = api_error
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_success(self, step, service_name):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Cluster"
        with patch.object(step, "kube") as kube_mock:
            kube_mock.get.return_value = service
            kube_mock.patch.return_value = None
            result = step.run(None)
        kube_mock.get.assert_called_once_with(
            lightkube.resources.core_v1.Service, name=service_name
        )
        kube_mock.patch.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_service_has_no_spec(self, step):
        service = Mock()
        service.spec = None
        with patch.object(step, "kube") as kube_mock:
            kube_mock.get.return_value = service
            result = step.run(None)
        assert result.result_type == ResultType.FAILED
        assert "Service has no spec" in result.message

    def test_run_patch_api_error(self, step):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Cluster"
        api_error = lightkube.core.exceptions.ApiError.__new__(
            lightkube.core.exceptions.ApiError
        )
        with patch.object(step, "kube") as kube_mock:
            kube_mock.get.return_value = service
            kube_mock.patch.side_effect = api_error
            result = step.run(None)
        assert result.result_type == ResultType.FAILED


class TestEnsureCiliumDeviceByHostStep:
    @pytest.fixture
    def deployment(self, basic_deployment):
        basic_deployment.name = "test-deployment"
        basic_deployment.get_space.return_value = "internal"
        return basic_deployment

    @pytest.fixture
    def control_nodes(self):
        return [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]

    @pytest.fixture
    def client(self, control_nodes):
        return Mock(
            cluster=Mock(
                list_nodes_by_role=Mock(return_value=control_nodes),
                get_config=Mock(return_value="{}"),
            )
        )

    @pytest.fixture
    def jhelper(self, basic_jhelper):
        return basic_jhelper

    @pytest.fixture
    def step(self, deployment, client, jhelper):
        step = EnsureCiliumDeviceByHostStep(deployment, client, jhelper, "test-model")
        step.kube = Mock()
        return step

    @pytest.fixture(autouse=True)
    def setup_patches(self, step):
        kubeconfig_mocker = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=Mock())),
        )
        kubeconfig_mocker.start()
        kube_mocker = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=Mock(return_value=step.kube)),
        )
        kube_mocker.start()
        yield
        kubeconfig_mocker.stop()
        kube_mocker.stop()

    def test_is_skip_no_changes(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=([], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_outdated_device(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=(["node1"], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1
        assert step.to_update[0]["name"] == "node1"

    def test_is_skip_missing_config(self, step, step_context):
        step._get_outdated_resources = Mock(return_value=(["node1", "node2"], []))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 2

    def test_is_skip_deleted_node(self, step, step_context):
        """Deleted nodes (not in control_nodes) are scheduled for cleanup."""
        step._get_outdated_resources = Mock(return_value=([], ["departed-node"]))
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_delete) == 1
        assert step.to_delete[0]["name"] == "departed-node"

    def test_is_skip_single_node_fqdn(self, deployment, client, jhelper, step_context):
        node_info = {"name": "node1", "machineid": "1", "role": ["control"]}
        client.cluster.get_node_info = Mock(return_value=node_info)
        step = EnsureCiliumDeviceByHostStep(
            deployment, client, jhelper, "test-model", fqdn="node1.maas"
        )
        step.kube = Mock()
        step._get_outdated_resources = Mock(return_value=(["node1"], []))
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.control_nodes == [node_info]

    def test_is_skip_single_node_fqdn_preserves_other_configs(
        self, deployment, client, jhelper, step_context
    ):
        """When fqdn is set, configs for other nodes must not be deleted.

        Regression test: during greenfield join, only the joining node is in
        the working set.  _get_outdated_resources marks other nodes' configs
        as deleted, but the base class must suppress deletions in single-node
        mode so previously-joined nodes keep their CiliumNodeConfig.
        """
        node_info = {"name": "node2", "machineid": "2", "role": ["control"]}
        client.cluster.get_node_info = Mock(return_value=node_info)
        step = EnsureCiliumDeviceByHostStep(
            deployment, client, jhelper, "test-model", fqdn="node2.maas"
        )
        step.kube = Mock()
        # node2 needs an update; node1 reported as deleted (not in working set)
        step._get_outdated_resources = Mock(return_value=(["node2"], ["node1"]))
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1
        assert step.to_update[0]["name"] == "node2"
        assert step.to_delete == []

    def test_is_skip_wrong_node_selector(self, step, control_nodes, jhelper):
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="internal"),
        }
        wrong_selector_config = Mock()
        wrong_selector_config.metadata = Mock(
            name="cilium-devices-node1",
            labels={
                "app.kubernetes.io/managed-by": "test-deployment",
                "sunbeam/hostname": "node1",
            },
        )
        wrong_selector_config.spec = {
            "nodeSelector": {"matchLabels": {"sunbeam/hostname": "wrong-node"}},
            "defaults": {"devices": "eth0"},
        }
        step.kube.list = Mock(return_value=[wrong_selector_config])
        outdated, deleted = step._get_outdated_resources(control_nodes, step.kube)
        assert "node1" in outdated

    def test_run_creates_config(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step._resolve_k8s_node_name = Mock(return_value="node1")
        step.kube.apply = Mock()
        step.kube.patch = Mock()

        # _find_cilium_pod returns the old pod
        old_pod = Mock()
        old_pod.metadata = Mock(name="cilium-abc")
        old_pod.spec = Mock(nodeName="node1")
        # _wait_for_cilium_ready returns a NEW pod (different name)
        new_pod = Mock()
        new_pod.metadata = Mock(name="cilium-xyz")
        new_pod.spec = Mock(nodeName="node1")
        new_pod.status = Mock(conditions=[Mock(type="Ready", status="True")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [old_pod]  # _find_cilium_pod
            return [new_pod]  # _wait_for_cilium_ready

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        result = step.run(None)

        step.kube.apply.assert_called_once()
        step.kube.patch.assert_called_once_with(
            step.cilium_node_config_resource,
            "cilium-devices-node1",
            {
                "metadata": {
                    "annotations": {
                        "sunbeam/restart-pending": "false",
                    }
                }
            },
            namespace="kube-system",
            patch_type=PatchType.MERGE,
        )
        assert result.result_type == ResultType.COMPLETED

    def test_run_updates_config(self, step):
        step.to_update = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        step.to_delete = []
        step._get_interface = Mock(side_effect=["eth0", "eth1"])
        step._resolve_k8s_node_name = Mock(side_effect=lambda n: n)
        step.kube.apply = Mock()
        step.kube.patch = Mock()

        # Old pods (to be deleted)
        old_pod1 = Mock()
        old_pod1.metadata = Mock(name="cilium-aaa")
        old_pod1.spec = Mock(nodeName="node1")
        old_pod2 = Mock()
        old_pod2.metadata = Mock(name="cilium-bbb")
        old_pod2.spec = Mock(nodeName="node2")
        # New replacement pods
        new_pod1 = Mock()
        new_pod1.metadata = Mock(name="cilium-new1")
        new_pod1.spec = Mock(nodeName="node1")
        new_pod1.status = Mock(conditions=[Mock(type="Ready", status="True")])
        new_pod2 = Mock()
        new_pod2.metadata = Mock(name="cilium-new2")
        new_pod2.spec = Mock(nodeName="node2")
        new_pod2.status = Mock(conditions=[Mock(type="Ready", status="True")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] in (1, 3):  # _find_cilium_pod calls
                return [old_pod1, old_pod2]
            if call_count[0] == 2:  # _wait after node1
                return [new_pod1, old_pod2]
            return [new_pod1, new_pod2]  # _wait after node2

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        result = step.run(None)

        assert step.kube.apply.call_count == 2
        assert step.kube.patch.call_count == 2  # clears restart-pending on both
        assert result.result_type == ResultType.COMPLETED

    def test_run_deletes_stale_config(self, step):
        step.to_update = []
        step.to_delete = [{"name": "node2"}]
        step._resolve_k8s_node_name = Mock(return_value="node2")
        step.kube.delete = Mock()

        old_pod = Mock()
        old_pod.metadata = Mock(name="cilium-xyz")
        old_pod.spec = Mock(nodeName="node2")
        new_pod = Mock()
        new_pod.metadata = Mock(name="cilium-new")
        new_pod.spec = Mock(nodeName="node2")
        new_pod.status = Mock(conditions=[Mock(type="Ready", status="True")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [old_pod]  # _find_cilium_pod
            return [new_pod]  # _wait_for_cilium_ready

        step.kube.list = Mock(side_effect=list_side_effect)

        result = step.run(None)

        assert step.kube.delete.call_count == 2  # config + pod
        assert result.result_type == ResultType.COMPLETED

    def test_run_api_error(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.apply = Mock(side_effect=api_error)

        result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "Failed to apply CiliumNodeConfig for node1" in result.message

    def test_run_no_interface_found(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(side_effect=MachineNotFoundException("not found"))

        result = step.run(None)

        assert result.result_type == ResultType.FAILED

    def test_run_cilium_pod_not_found(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step._resolve_k8s_node_name = Mock(return_value="node1")
        step.kube.apply = Mock()
        step.kube.list = Mock(return_value=[])

        result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "No cilium pod found on node node1" in result.message

    def test_run_restart_timeout(self, step):
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step._resolve_k8s_node_name = Mock(return_value="node1")
        step.kube.apply = Mock()

        old_pod = Mock()
        old_pod.metadata = Mock(name="cilium-abc")
        old_pod.spec = Mock(nodeName="node1")
        # Replacement pod exists but never becomes Ready
        not_ready_pod = Mock()
        not_ready_pod.metadata = Mock(name="cilium-new")
        not_ready_pod.spec = Mock(nodeName="node1")
        not_ready_pod.status = Mock(conditions=[Mock(type="Ready", status="False")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [old_pod]  # _find_cilium_pod
            return [not_ready_pod]  # _wait_for_cilium_ready

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        with (
            patch("sunbeam.steps.k8s.time.monotonic", side_effect=[0.0, 301.0]),
            patch("sunbeam.steps.k8s.time.sleep"),
        ):
            result = step.run(None)

        assert result.result_type == ResultType.FAILED
        assert "did not become Ready" in result.message

    def test_run_old_pod_ignored_during_readiness_wait(self, step):
        """The terminating pod (same name as deleted) must not satisfy readiness."""
        step.to_update = [{"name": "node1", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step._resolve_k8s_node_name = Mock(return_value="node1")
        step.kube.apply = Mock()
        step.kube.patch = Mock()

        old_pod = Mock()
        old_pod.metadata = Mock(name="cilium-abc")
        old_pod.spec = Mock(nodeName="node1")
        # Old pod still shows as Ready (terminating but not gone yet)
        old_pod.status = Mock(conditions=[Mock(type="Ready", status="True")])
        # New pod eventually becomes Ready
        new_pod = Mock()
        new_pod.metadata = Mock(name="cilium-new")
        new_pod.spec = Mock(nodeName="node1")
        new_pod.status = Mock(conditions=[Mock(type="Ready", status="True")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [old_pod]  # _find_cilium_pod
            if call_count[0] == 2:
                return [old_pod]  # _wait: old pod still around, should skip
            return [new_pod]  # _wait: new pod appears

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        with patch("sunbeam.steps.k8s.time.monotonic", side_effect=[0.0, 1.0, 2.0]):
            with patch("sunbeam.steps.k8s.time.sleep"):
                result = step.run(None)

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_restart_pending(self, step, control_nodes, jhelper):
        """Config with correct device but restart-pending=true is outdated."""
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="internal"),
        }
        config = Mock()
        config.metadata = Mock(
            name="cilium-devices-node1",
            labels={
                "app.kubernetes.io/managed-by": "test-deployment",
                "sunbeam/hostname": "node1",
            },
            annotations={"sunbeam/restart-pending": "true"},
        )
        config.spec = {
            "nodeSelector": {"matchLabels": {"sunbeam/hostname": "node1"}},
            "defaults": {"devices": "eth0"},
        }
        step.kube.list = Mock(return_value=[config])
        outdated, deleted = step._get_outdated_resources(control_nodes, step.kube)
        assert "node1" in outdated

    def test_resolve_k8s_node_name(self, step):
        """_resolve_k8s_node_name queries K8s node by sunbeam/hostname label."""
        k8s_node = Mock()
        k8s_node_meta = Mock()
        k8s_node_meta.name = "node1"
        k8s_node.metadata = k8s_node_meta

        with patch("sunbeam.steps.k8s.list_nodes", return_value=[k8s_node]):
            result = step._resolve_k8s_node_name("node1.maas")

        assert result == "node1"

    def test_resolve_k8s_node_name_not_found(self, step):
        """_resolve_k8s_node_name raises when no K8s node matches."""
        with patch("sunbeam.steps.k8s.list_nodes", return_value=[]):
            with pytest.raises(SunbeamException, match="No K8s node found"):
                step._resolve_k8s_node_name("node1.maas")

    def test_run_fqdn_vs_short_hostname(self, step):
        """Full flow: sunbeam FQDN resolved to K8s short hostname for pod lookup."""
        step.to_update = [{"name": "node1.maas", "machineid": "1"}]
        step.to_delete = []
        step._get_interface = Mock(return_value="eth0")
        step.kube.apply = Mock()
        step.kube.patch = Mock()

        # _resolve_k8s_node_name returns the short hostname
        k8s_node = Mock()
        k8s_node_meta = Mock()
        k8s_node_meta.name = "node1"
        k8s_node.metadata = k8s_node_meta

        # k8s pod has short hostname in spec.nodeName
        old_pod = Mock()
        old_pod.metadata = Mock(name="cilium-abc")
        old_pod.spec = Mock(nodeName="node1")
        new_pod = Mock()
        new_pod.metadata = Mock(name="cilium-xyz")
        new_pod.spec = Mock(nodeName="node1")
        new_pod.status = Mock(conditions=[Mock(type="Ready", status="True")])

        call_count = [0]

        def list_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [old_pod]  # _find_cilium_pod
            return [new_pod]  # _wait_for_cilium_ready

        step.kube.list = Mock(side_effect=list_side_effect)
        step.kube.delete = Mock()

        with patch("sunbeam.steps.k8s.list_nodes", return_value=[k8s_node]):
            result = step.run(None)

        step.kube.apply.assert_called_once()
        step.kube.patch.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_resolve_k8s_node_name_multiple_matches(self, step):
        """_resolve_k8s_node_name raises when multiple K8s nodes share the label."""
        node1 = Mock()
        node1_meta = Mock()
        node1_meta.name = "node1-a"
        node1.metadata = node1_meta
        node2 = Mock()
        node2_meta = Mock()
        node2_meta.name = "node1-b"
        node2.metadata = node2_meta

        with patch("sunbeam.steps.k8s.list_nodes", return_value=[node1, node2]):
            with pytest.raises(SunbeamException, match="Multiple K8s nodes found"):
                step._resolve_k8s_node_name("node1.maas")

    def test_resolve_k8s_node_name_k8s_error(self, step):
        """_resolve_k8s_node_name raises when the K8s API call fails."""
        with patch("sunbeam.steps.k8s.list_nodes", side_effect=K8SError("api down")):
            with pytest.raises(SunbeamException, match="Failed to resolve"):
                step._resolve_k8s_node_name("node1.maas")

    def test_resolve_k8s_node_name_no_metadata(self, step):
        """_resolve_k8s_node_name raises when matched node has no metadata."""
        k8s_node = Mock()
        k8s_node.metadata = None

        with patch("sunbeam.steps.k8s.list_nodes", return_value=[k8s_node]):
            with pytest.raises(SunbeamException, match="has no metadata.name"):
                step._resolve_k8s_node_name("node1.maas")

    def test_run_delete_node_already_gone(self, step):
        """If K8s node is gone, config is deleted and restart is skipped."""
        step.to_update = []
        step.to_delete = [{"name": "gone-node.maas"}]
        step.kube.delete = Mock()

        with patch("sunbeam.steps.k8s.list_nodes", return_value=[]):
            result = step.run(None)

        # Config deletion should still happen
        step.kube.delete.assert_called_once()
        # Step completes (restart failure is swallowed for delete path)
        assert result.result_type == ResultType.COMPLETED
