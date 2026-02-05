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

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    JujuException,
    LeaderNotFoundException,
)
from sunbeam.steps.k8s import (
    CREDENTIAL_SUFFIX,
    K8S_CLOUD_SUFFIX,
    AddK8SCloudStep,
    AddK8SCredentialStep,
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
    deployment.get_space.return_value = "management"
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

    def test_is_skip(self, setup_deployment, jhelper, cloud_name):
        clouds = {}
        jhelper.get_clouds.return_value = clouds

        step = AddK8SCloudStep(setup_deployment, jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_cloud_already_deployed(
        self, setup_deployment, jhelper, cloud_name
    ):
        clouds = {cloud_name: {"endpoint": "10.0.10.1"}}
        jhelper.get_clouds.return_value = clouds

        step = AddK8SCloudStep(setup_deployment, jhelper)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, setup_deployment, jhelper, cloud_name):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCloudStep(setup_deployment, jhelper)
            result = step.run()

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

    def test_is_skip(self, deployment, jhelper):
        credentials = {}
        jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(deployment, jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_credential_exists(self, deployment, jhelper, credential_name):
        credentials = {"controller-credentials": {credential_name: {}}}
        jhelper.get_credentials.return_value = credentials

        step = AddK8SCredentialStep(deployment, jhelper)
        with patch.object(step, "get_credentials", return_value=credentials):
            result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_run(self, deployment, jhelper, cloud_name, credential_name):
        with patch("sunbeam.steps.k8s.read_config", Mock(return_value={})):
            step = AddK8SCredentialStep(deployment, jhelper)
            result = step.run()

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

    def test_is_skip(self, deployment, client, jhelper):
        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_config_missing(self, deployment, client, jhelper):
        with patch(
            "sunbeam.steps.k8s.read_config",
            Mock(side_effect=ConfigItemNotFoundException),
        ):
            step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
            result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_run(self, deployment, client, jhelper):
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
        result = step.run()

        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_application_not_found(self, deployment, client, jhelper):
        jhelper.get_leader_unit.side_effect = ApplicationNotFoundException(
            "Application missing..."
        )

        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run()

        jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Application missing..."

    def test_run_leader_not_found(self, deployment, client, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )

        step = StoreK8SKubeConfigStep(deployment, client, jhelper, "test-model")
        result = step.run()

        jhelper.get_leader_unit.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Leader missing..."

    def test_run_action_failed(self, deployment, client, jhelper):
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
        result = step.run()

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

    def test_is_skip_no_outdated_or_deleted(self, step):
        step._get_outdated_l2_advertisement = Mock(return_value=([], []))
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_outdated(self, step):
        step._get_outdated_l2_advertisement = Mock(return_value=(["node1"], []))
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_update) == 1

    def test_is_skip_with_deleted(self, step):
        step._get_outdated_l2_advertisement = Mock(return_value=([], ["node2"]))
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert len(step.to_delete) == 1

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
        network = Mock()
        result = step._get_interface({"name": "node1"}, network)
        assert result == "eth0"

    def test_get_interface_found(self, step, jhelper, deployment):
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="management"),
            "eth1": Mock(space="other-space"),
        }
        deployment.get_space.return_value = "management"
        network = Mock()
        result = step._get_interface({"name": "node1", "machineid": "1"}, network)
        assert result == "eth0"
        assert step._ifnames["node1"] == "eth0"

    def test_get_interface_not_found(self, step, jhelper, deployment):
        """Test that _get_interface raises exception when interface is not found."""
        jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="other-space"),
            "eth1": Mock(space="another-space"),
        }
        deployment.get_space.return_value = "management"
        network = Mock()
        network.name = "test-network"

        # Test the private method directly - it should raise an exception
        # Using a standard exception pattern instead of accessing the
        # private exception class
        with pytest.raises(Exception) as exc_info:
            step._get_interface({"name": "node1", "machineid": "1"}, network)

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

    def _get_interface(node, network):
        for node_it in nodes:
            if node_it["name"] == node["name"]:
                return node_it["interface"]
        raise RuntimeError(
            f"Node {node['name']} has no interface in network space '{network}'"
        )

    step._get_interface = Mock(side_effect=_get_interface)

    outdated_res, deleted_res = step._get_outdated_l2_advertisement(nodes, kube)

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

    def test_is_skip_kubeconfig_not_found(self, deployment, client, jhelper):
        with patch(
            "sunbeam.steps.k8s.read_config", side_effect=ConfigItemNotFoundException
        ):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert "kubeconfig not found" in result.message

    def test_is_skip_l2_advertisement_not_found(
        self, deployment, client, jhelper, kube
    ):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=404)
        kube.get = Mock(side_effect=api_error)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_api_error_other(
        self, deployment, client, jhelper, kube
    ):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(side_effect=api_error)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_l2_advertisement_already_muted(
        self, deployment, client, jhelper, kube, node_selectors
    ):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": node_selectors}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_needs_muting(
        self, deployment, client, jhelper, kube
    ):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": [{"matchLabels": {"foo": "bar"}}]}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(deployment, client, jhelper)
            result = step.is_skip()
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

    def test_is_skip_no_nodes_to_update(self, step, client, jhelper):
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
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_nodes_to_update(self, step, client, jhelper):
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
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert "node2" in step.to_update

    def test_is_skip_nodes_to_update_with_fqdn(self, step, client, jhelper):
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
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert "node2.maas" in step.to_update

    def test_is_skip_kube_client_error(self, step, client):
        client.cluster.list_nodes_by_role.return_value = []
        with patch(
            "sunbeam.steps.k8s.get_kube_client", side_effect=KubeClientError("fail")
        ):
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_k8s_api_error(self, step, client, jhelper):
        client.cluster.list_nodes_by_role.return_value = [
            {"name": "node1", "machineid": "1"}
        ]
        jhelper.get_machines.return_value = {
            "1": Mock(
                network_interfaces={
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.1"])
                }
            ),
            "2": Mock(
                network_interfaces={
                    "eth0": Mock(space="management", ip_addresses=["10.0.0.2"])
                }
            ),
        }
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        step.kube.list.side_effect = api_error
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_missing(self, step, client, jhelper):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
        ]
        client.cluster.list_nodes_by_role.return_value = control_nodes
        step.kube.list.return_value = [Mock(metadata=Mock(name="node1", labels={}))]
        jhelper.get_machines.return_value = {}
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_not_control_role(self, step, client):
        step.fqdn = "node1"
        client.cluster.get_node_info.return_value = {
            "name": "node1",
            "machineid": "1",
            "role": "compute",
        }
        result = step.is_skip()
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

    def test_is_skip(self, step, kube):
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
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_kube_get_error(self, step, kube):
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
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_hpa_already_exists(self, step, kube):
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
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_new_control_nodes_added(self, step, kube):
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
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert step.replica_count == 3

    def test_is_skip_control_nodes_removed(self, step, kube):
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
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert step.replica_count == 1

    def test_run(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        result = step.run(None)
        assert result.result_type == ResultType.COMPLETED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_helm_upgrade_failed(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=1)
        result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_failed_on_juju_run_on_machine_unit(self, step, jhelper):
        jhelper.run_cmd_on_machine_unit_payload.side_effect = JujuException(
            "Not able to run command"
        )
        result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_leader_not_found(self, step, jhelper):
        jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )
        result = step.run(None)
        assert result.result_type == ResultType.FAILED
        jhelper.get_leader_unit.assert_called_once()
        jhelper.run_cmd_on_machine_unit_payload.assert_not_called()


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

    def test_is_skip_external_traffic_policy_already_local(self, step, service_name):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Local"
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_external_traffic_policy_not_local(self, step, service_name):
        service = Mock()
        service.spec = Mock()
        service.spec.externalTrafficPolicy = "Cluster"
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_service_has_no_spec(self, step, service_name):
        service = Mock()
        service.spec = None
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.return_value = service
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_kube_client_error(self, step):
        with patch(
            "sunbeam.steps.k8s.get_kube_client", side_effect=KubeClientError("fail")
        ):
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_api_error(self, step):
        api_error = lightkube.core.exceptions.ApiError.__new__(
            lightkube.core.exceptions.ApiError
        )
        with patch("sunbeam.steps.k8s.get_kube_client", return_value=step.kube):
            step.kube.get.side_effect = api_error
            result = step.is_skip()
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
