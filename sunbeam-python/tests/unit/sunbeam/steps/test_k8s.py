# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ipaddress
import json
import unittest
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
    StoreK8SKubeConfigStep,
    _get_kube_client,
    _get_machines_space_ips,
)


class TestAddK8SCloudStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.deployment = Mock()
        self.cloud_name = f"{self.deployment.name}{K8S_CLOUD_SUFFIX}"
        self.deployment.get_client().cluster.get_config.return_value = "{}"
        self.jhelper = Mock()

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
        self.jhelper = Mock()

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
        self.jhelper = Mock()
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
        self.jhelper.get_leader_unit_machine.return_value = "0"
        self.jhelper.get_space_networks.return_value = {}
        self.jhelper.get_machine_interfaces.return_value = {
            "enp0s8": Mock(
                ip_addresses=["127.0.0.1"],
                space="management",
            )
        }

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
        self.jhelper.get_leader_unit_machine.return_value = "0"
        self.jhelper.get_space_networks.return_value = {}
        self.jhelper.get_machine_interfaces.return_value = {
            "enp0s8": Mock(
                ip_addresses=["127.0.0.1"],
                space="management",
            )
        }
        step = StoreK8SKubeConfigStep(
            self.deployment, self.client, self.jhelper, "test-model"
        )
        result = step.run()

        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_action.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert result.message == "Action failed..."


class TestEnsureL2AdvertisementByHostStep(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)

    def setUp(self):
        self.deployment = Mock()
        self.control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        self.client = Mock(
            cluster=Mock(
                list_nodes_by_role=Mock(return_value=self.control_nodes),
                get_config=Mock(return_value="{}"),
            )
        )
        self.jhelper = Mock()
        self.model = "test-model"
        self.network = Mock()
        self.pool = "test-pool"
        self.step = EnsureL2AdvertisementByHostStep(
            self.deployment,
            self.client,
            self.jhelper,
            self.model,
            self.network,
            self.pool,
        )
        self.step.kube = Mock()
        self.step.kubeconfig = Mock()

        self.kubeconfig_mocker = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=self.step.kubeconfig)),
        )
        self.kubeconfig_mocker.start()
        self.kube_mocker = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=Mock(return_value=self.step.kube)),
        )
        self.kube_mocker.start()

    def test_is_skip_no_outdated_or_deleted(self):
        self.step._get_outdated_l2_advertisement = Mock(return_value=([], []))
        result = self.step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_outdated(self):
        self.step._get_outdated_l2_advertisement = Mock(return_value=(["node1"], []))
        result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert len(self.step.to_update) == 1

    def test_is_skip_with_deleted(self):
        self.step._get_outdated_l2_advertisement = Mock(return_value=([], ["node2"]))
        result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert len(self.step.to_delete) == 1

    def test_run_update_and_delete(self):
        self.step.to_update = [{"name": "node1", "machineid": "1"}]
        self.step.to_delete = [{"name": "node2", "machineid": "2"}]
        self.step._get_interface = Mock(return_value="eth0")
        self.step.kube.apply = Mock()
        self.step.kube.delete = Mock()

        result = self.step.run(None)

        self.step.kube.apply.assert_called_once()
        self.step.kube.delete.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_update_failure(self):
        self.step.to_update = [{"name": "node1", "machineid": "1"}]
        self.step.to_delete = []
        self.step._get_interface = Mock(return_value="eth0")
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        self.step.kube.apply = Mock(side_effect=api_error)

        result = self.step.run(None)

        self.step.kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED

    def test_run_delete_failure(self):
        self.step.to_update = []
        self.step.to_delete = [{"name": "node2", "machineid": "2"}]
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        self.step.kube.delete = Mock(side_effect=api_error)

        result = self.step.run(None)

        self.step.kube.delete.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_get_interface_cached(self):
        self.step._ifnames = {"node1": "eth0"}
        result = self.step._get_interface({"name": "node1"}, self.network)
        assert result == "eth0"

    def test_get_interface_found(self):
        self.jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="management"),
            "eth1": Mock(space="other-space"),
        }
        self.deployment.get_space.return_value = "management"
        result = self.step._get_interface(
            {"name": "node1", "machineid": "1"}, self.network
        )
        assert result == "eth0"
        assert self.step._ifnames["node1"] == "eth0"

    def test_get_interface_not_found(self):
        self.jhelper.get_machine_interfaces.return_value = {
            "eth0": Mock(space="other-space"),
            "eth1": Mock(space="another-space"),
        }
        self.deployment.get_space.return_value = "management"
        with self.assertRaises(EnsureL2AdvertisementByHostStep._L2AdvertisementError):
            self.step._get_interface({"name": "node1", "machineid": "1"}, self.network)

    def test_ensure_l2_advertisement_retry(self):
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
        self.step.kube.apply.side_effect = [api_error, None]
        self.step._ensure_l2_advertisement.retry.wait = tenacity.wait_none()
        self.step._ensure_l2_advertisement("node1", "eth0")


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
        raise EnsureL2AdvertisementByHostStep._L2AdvertisementError()

    step._get_interface = Mock(side_effect=_get_interface)

    outdated_res, deleted_res = step._get_outdated_l2_advertisement(nodes, kube)

    assert outdated_res == outdated
    assert deleted_res == deleted


class TestEnsureDefaultL2AdvertisementMutedStep(unittest.TestCase):
    def setUp(self):
        self.deployment = Mock()
        self.deployment.name = "test-deployment"
        self.client = Mock()
        self.jhelper = Mock()
        self.kubeconfig = Mock()
        self.kube = Mock()
        self.l2_advertisement_resource = Mock()
        self.l2_advertisement_namespace = "test-namespace"
        self.default_l2_advertisement = "default-pool"
        self.node_selectors = [
            {
                "matchLabels": {
                    "kubernetes.io/hostname": "not-existing.sunbeam",
                }
            }
        ]

        # Patch K8SHelper static methods
        self.k8shelper_patch = patch.multiple(
            "sunbeam.steps.k8s.K8SHelper",
            get_lightkube_l2_advertisement_resource=Mock(
                return_value=self.l2_advertisement_resource
            ),
            get_loadbalancer_namespace=Mock(
                return_value=self.l2_advertisement_namespace
            ),
            get_internal_pool_name=Mock(return_value=self.default_l2_advertisement),
            get_kubeconfig_key=Mock(return_value="kubeconfig-key"),
        )
        self.k8shelper_patch.start()

        # Patch l_kubeconfig and l_client
        self.kubeconfig_patch = patch(
            "sunbeam.steps.k8s.l_kubeconfig.KubeConfig",
            Mock(from_dict=Mock(return_value=self.kubeconfig)),
        )
        self.kubeconfig_patch.start()
        self.kube_patch = patch(
            "sunbeam.steps.k8s.l_client.Client",
            Mock(return_value=self.kube),
        )
        self.kube_patch.start()

        # Patch meta_v1
        self.meta_v1_patch = patch(
            "sunbeam.steps.k8s.meta_v1.ObjectMeta",
            Mock(return_value=Mock()),
        )
        self.meta_v1_patch.start()

        self.addCleanup(self.k8shelper_patch.stop)
        self.addCleanup(self.kubeconfig_patch.stop)
        self.addCleanup(self.kube_patch.stop)
        self.addCleanup(self.meta_v1_patch.stop)

    def test_is_skip_kubeconfig_not_found(self):
        with patch(
            "sunbeam.steps.k8s.read_config", side_effect=ConfigItemNotFoundException
        ):
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED
        assert "kubeconfig not found" in result.message

    def test_is_skip_l2_advertisement_not_found(self):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=404)
        self.kube.get = Mock(side_effect=api_error)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_api_error_other(self):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            self.kube.get = Mock(side_effect=api_error)
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            result = step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_l2_advertisement_already_muted(self):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": self.node_selectors}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            self.kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_l2_advertisement_needs_muting(self):
        l2_advertisement = Mock()
        l2_advertisement.spec = {"nodeSelectors": [{"matchLabels": {"foo": "bar"}}]}
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            self.kube.get = Mock(return_value=l2_advertisement)
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_success(self):
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            step.kube = self.kube
            self.kube.apply = Mock(return_value=None)
            result = step.run(None)
        self.kube.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_api_error(self):
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        with patch("sunbeam.steps.k8s.read_config", return_value={}):
            step = EnsureDefaultL2AdvertisementMutedStep(
                self.deployment, self.client, self.jhelper
            )
            step.kube = self.kube
            self.kube.apply = Mock(side_effect=api_error)
            result = step.run(None)
        self.kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED
        assert "Failed to update L2 default advertisement" in result.message


class TestEnsureK8SUnitsTaggedStep(unittest.TestCase):
    def setUp(self):
        self.deployment = Mock()
        self.deployment.name = "test-deployment"
        self.deployment.get_space.return_value = "management"
        self.client = Mock()
        self.jhelper = Mock()
        self.jhelper.get_space_networks.return_value = [
            ipaddress.ip_network("10.0.0.0/8")
        ]
        self.model = "test-model"
        self.step = EnsureK8SUnitsTaggedStep(
            self.deployment, self.client, self.jhelper, self.model
        )
        self.kube = Mock()
        self.step.kube = self.kube

    def test_is_skip_no_nodes_to_update(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        self.client.cluster.list_nodes_by_role.return_value = control_nodes
        self.kube.list.return_value = [
            _to_kube_object(
                {"name": "node1", "labels": {"sunbeam/hostname": "node1"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.1")]},
            ),
            _to_kube_object(
                {"name": "node2", "labels": {"sunbeam/hostname": "node2"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.2")]},
            ),
        ]
        self.jhelper.get_machines.return_value = {
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
        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_nodes_to_update(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        self.client.cluster.list_nodes_by_role.return_value = control_nodes
        self.kube.list.return_value = [
            _to_kube_object(
                {"name": "node1", "labels": {"sunbeam/hostname": "node1"}},
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.1")]},
            ),
            _to_kube_object(
                {"name": "node2", "labels": {}},  # Missing label
                status={"addresses": [Mock(type="InternalIP", address="10.0.0.2")]},
            ),
        ]
        self.jhelper.get_machines.return_value = {
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
        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert "node2" in self.step.to_update

    def test_is_skip_kube_client_error(self):
        self.client.cluster.list_nodes_by_role.return_value = []
        with patch(
            "sunbeam.steps.k8s._get_kube_client", side_effect=KubeClientError("fail")
        ):
            result = self.step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_k8s_api_error(self):
        self.client.cluster.list_nodes_by_role.return_value = [
            {"name": "node1", "machineid": "1"}
        ]
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        self.kube.list.side_effect = api_error
        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_missing(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
        ]
        self.client.cluster.list_nodes_by_role.return_value = control_nodes
        self.kube.list.return_value = [Mock(metadata=Mock(name="node1", labels={}))]
        self.jhelper.get_machines.return_value = {}
        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_machine_not_control_role(self):
        self.step.fqdn = "node1"
        self.client.cluster.get_node_info.return_value = {
            "name": "node1",
            "machineid": "1",
            "role": "compute",
        }
        result = self.step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_run_success(self):
        self.step.to_update = {"node1": "k8s-node1"}
        self.kube.apply = Mock()
        with (
            patch("sunbeam.steps.k8s.core_v1.Node", Mock()),
            patch("sunbeam.steps.k8s.meta_v1.ObjectMeta", Mock()),
        ):
            result = self.step.run(None)
        self.kube.apply.assert_called_once()
        assert result.result_type == ResultType.COMPLETED

    def test_run_apply_failure(self):
        self.step.to_update = {"node1": "k8s-node1"}
        api_error = ApiError.__new__(ApiError)
        api_error.status = Mock(code=500)
        self.kube.apply = Mock(side_effect=api_error)
        with (
            patch("sunbeam.steps.k8s.core_v1.Node", Mock()),
            patch("sunbeam.steps.k8s.meta_v1.ObjectMeta", Mock()),
        ):
            result = self.step.run(None)
        self.kube.apply.assert_called_once()
        assert result.result_type == ResultType.FAILED


class TestGetKubeClient(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.namespace = "test-namespace"

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
    ):
        mock_read_config.return_value = {"apiVersion": "v1"}
        mock_kubeconfig_from_dict.return_value = Mock()

        result = _get_kube_client(self.client, self.namespace)

        mock_read_config.assert_called_once_with(self.client, "kubeconfig-key")
        mock_kubeconfig_from_dict.assert_called_once_with({"apiVersion": "v1"})
        mock_client.assert_called_once_with(
            mock_kubeconfig_from_dict.return_value,
            self.namespace,
            trust_env=False,
        )
        assert result == mock_client.return_value

    @patch("sunbeam.steps.k8s.read_config", side_effect=ConfigItemNotFoundException)
    @patch(
        "sunbeam.steps.k8s.K8SHelper.get_kubeconfig_key", return_value="kubeconfig-key"
    )
    def test_get_kube_client_config_not_found(
        self, mock_get_kubeconfig_key, mock_read_config
    ):
        with self.assertRaises(KubeClientError) as context:
            _get_kube_client(self.client, self.namespace)

        mock_read_config.assert_called_once_with(self.client, "kubeconfig-key")
        assert "K8S kubeconfig not found" in str(context.exception)

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
    ):
        mock_read_config.return_value = {"apiVersion": "v1"}
        mock_kubeconfig_from_dict.return_value = Mock()

        with self.assertRaises(KubeClientError) as context:
            _get_kube_client(self.client, self.namespace)

        mock_read_config.assert_called_once_with(self.client, "kubeconfig-key")
        mock_kubeconfig_from_dict.assert_called_once_with({"apiVersion": "v1"})
        mock_client.assert_called_once_with(
            mock_kubeconfig_from_dict.return_value,
            self.namespace,
            trust_env=False,
        )
        assert "Error creating k8s client" in str(context.exception)


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


class TestPatchCoreDNSStep(unittest.TestCase):
    def setUp(self):
        self.deployment = Mock()
        self.client = Mock()
        self.jhelper = Mock()
        self.step = PatchCoreDNSStep(self.deployment, self.jhelper)
        self.kube = Mock()

    def test_is_skip(self):
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
        self.kube.get = Mock(side_effect=api_error)

        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_kube_get_error(self):
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
        self.kube.get = Mock(side_effect=api_error)

        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            result = self.step.is_skip()
        assert result.result_type == ResultType.FAILED

    def test_is_skip_hpa_already_exists(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        self.step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 1

        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            self.kube.get = Mock(return_value=hpa)
            result = self.step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_new_control_nodes_added(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
            {"name": "node3", "machineid": "3"},
        ]
        self.step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 1

        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            self.kube.get = Mock(return_value=hpa)
            result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert self.step.replica_count == 3

    def test_is_skip_control_nodes_removed(self):
        control_nodes = [
            {"name": "node1", "machineid": "1"},
            {"name": "node2", "machineid": "2"},
        ]
        self.step.client.cluster.list_nodes_by_role.return_value = control_nodes
        hpa = Mock()
        hpa.spec = Mock()
        hpa.spec.minReplicas = 3

        with patch("sunbeam.steps.k8s._get_kube_client", return_value=self.kube):
            self.kube.get = Mock(return_value=hpa)
            result = self.step.is_skip()
        assert result.result_type == ResultType.COMPLETED
        assert self.step.replica_count == 1

    def test_run(self):
        self.jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=0)
        result = self.step.run(None)
        assert result.result_type == ResultType.COMPLETED
        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_helm_upgrade_failed(self):
        self.jhelper.run_cmd_on_machine_unit_payload.return_value = Mock(return_code=1)
        result = self.step.run(None)
        assert result.result_type == ResultType.FAILED
        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_failed_on_juju_run_on_machine_unit(self):
        self.jhelper.run_cmd_on_machine_unit_payload.side_effect = JujuException(
            "Not able to run command"
        )
        result = self.step.run(None)
        assert result.result_type == ResultType.FAILED
        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_cmd_on_machine_unit_payload.assert_called_once()

    def test_run_leader_not_found(self):
        self.jhelper.get_leader_unit.side_effect = LeaderNotFoundException(
            "Leader missing..."
        )
        result = self.step.run(None)
        assert result.result_type == ResultType.FAILED
        self.jhelper.get_leader_unit.assert_called_once()
        self.jhelper.run_cmd_on_machine_unit_payload.assert_not_called()
