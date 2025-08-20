# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest
from requests.exceptions import HTTPError

import sunbeam.clusterd.service as service
import sunbeam.core.questions
from sunbeam.clusterd.cluster import ClusterService
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.steps.clusterd import (
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterInitStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateJujuControllerStep,
    ClusterUpdateNodeStep,
    DeploySunbeamClusterdApplicationStep,
    SaveManagementCidrStep,
)


@pytest.fixture()
def cclient():
    yield Mock()


@pytest.fixture()
def model():
    return "test-model"


@pytest.fixture()
def load_answers():
    with patch.object(sunbeam.core.questions, "load_answers") as p:
        yield p


class TestClusterdSteps:
    """Unit tests for sunbeam clusterd steps."""

    def test_init_step(self, cclient, mocker):
        role = "control"
        init_step = ClusterInitStep(cclient, [role], 0, "10.0.0.0/16")
        init_step.client = MagicMock()
        init_step.fqdn = "node1"
        mocker.patch("sunbeam.utils.get_local_ip_by_cidr", return_value="10.0.0.2")
        result = init_step.run()
        assert result.result_type == ResultType.COMPLETED
        init_step.client.cluster.bootstrap.assert_called_once()

    def test_init_step_ipv6(self, cclient, mocker):
        role = "control"
        init_step = ClusterInitStep(cclient, [role], 0, "fd00::/108")
        init_step.client = MagicMock()
        init_step.fqdn = "node1"
        mocker.patch("sunbeam.utils.get_local_ip_by_cidr", return_value="fd00::2")
        result = init_step.run()
        assert result.result_type == ResultType.COMPLETED
        init_step.client.cluster.bootstrap.assert_called_once()

    def test_add_node_step(self, cclient):
        add_node_step = ClusterAddNodeStep(cclient, name="node-1")
        add_node_step.client = MagicMock()
        result = add_node_step.run()
        assert result.result_type == ResultType.COMPLETED
        add_node_step.client.cluster.add_node.assert_called_once_with(name="node-1")

    def test_join_node_step(self, cclient):
        join_node_step = ClusterJoinNodeStep(
            cclient,
            token="TESTTOKEN",
            host_address="10.0.0.3",
            fqdn="node1",
            role=["control"],
        )
        join_node_step.client = MagicMock()
        result = join_node_step.run()
        assert result.result_type == ResultType.COMPLETED
        join_node_step.client.cluster.join_node.assert_called_once()

    def test_list_node_step(self, cclient):
        list_node_step = ClusterListNodeStep(cclient)
        list_node_step.client = MagicMock()
        result = list_node_step.run()
        assert result.result_type == ResultType.COMPLETED
        list_node_step.client.cluster.get_cluster_members.assert_called_once()

    def test_update_node_step(self, cclient):
        update_node_step = ClusterUpdateNodeStep(
            cclient, name="node-2", role=["control"], machine_id=1
        )
        update_node_step.client = MagicMock()
        result = update_node_step.run()
        assert result.result_type == ResultType.COMPLETED
        update_node_step.client.cluster.update_node_info.assert_called_once_with(
            "node-2", ["control"], 1
        )

    def test_remove_node_step(self, cclient):
        remove_node_step = ClusterRemoveNodeStep(cclient, name="node-2")
        remove_node_step.client = MagicMock()
        result = remove_node_step.run()
        assert result.result_type == ResultType.COMPLETED
        remove_node_step.client.cluster.remove_node.assert_called_once_with("node-2")

    def test_add_juju_user_step(self, cclient):
        add_juju_user_step = ClusterAddJujuUserStep(
            cclient, name="node-2", token="FAKETOKEN"
        )
        add_juju_user_step.client = MagicMock()
        result = add_juju_user_step.run()
        assert result.result_type == ResultType.COMPLETED
        add_juju_user_step.client.cluster.add_juju_user.assert_called_once_with(
            "node-2", "FAKETOKEN"
        )


class TestClusterService:
    """Unit tests for ClusterService."""

    def _mock_response(
        self, status=200, content="MOCKCONTENT", json_data=None, raise_for_status=None
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.content = content

        if json_data:
            mock_resp.json.return_value = json_data

        if raise_for_status:
            mock_resp.raise_for_status.side_effect = raise_for_status

        return mock_resp

    def test_bootstrap(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.bootstrap_cluster("node-1", "10.10.1.10:7000")

    def test_bootstrap_when_node_already_exists(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 500,
            "error": (
                "Failed to initialize local remote entry: "
                'A remote with name "node-1" already exists'
            ),
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=500,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.NodeAlreadyExistsException):
            cs.bootstrap_cluster("node-1", "10.10.1.10:7000")

    def test_generate_token(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": "TESTTOKEN",
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        token = cs.generate_token("node-2")
        assert token == "TESTTOKEN"

    def test_generate_token_when_token_already_exists(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 500,
            "error": "UNIQUE constraint failed: internal_token_records.name",
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=500,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.TokenAlreadyGeneratedException):
            cs.generate_token("node-2")

    def test_join(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.join("node-2", "10.10.1.11:7000", "TESTTOKEN")

    def test_join_with_wrong_token(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 500,
            "error": "Failed to join cluster with the given join token",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=500,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.NodeJoinException):
            cs.join("node-2", "10.10.1.11:7000", "TESTTOKEN")

    def test_join_when_node_already_joined(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 500,
            "error": (
                "Failed to initialize local remote entry: "
                'A remote with name "node-2" already exists'
            ),
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=500,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.NodeAlreadyExistsException):
            cs.join("node-2", "10.10.1.11:7000", "TESTTOKEN")

    def test_get_cluster_members(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": [
                {
                    "name": "node-1",
                    "address": "10.10.1.10:7000",
                    "certificate": "FAKECERT",
                    "role": "PENDING",
                    "schema_version": 1,
                    "last_heartbeat": "0001-01-01T00:00:00Z",
                    "status": "ONLINE",
                    "secret": "",
                }
            ],
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        members = cs.get_cluster_members()
        members_from_call = [m.get("name") for m in members]
        members_from_mock = [m.get("name") for m in json_data.get("metadata")]
        assert members_from_mock == members_from_call

    def test_get_cluster_members_when_cluster_not_initialised(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 500,
            "error": "Database is not yet initialized",
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=500,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.ClusterServiceUnavailableException):
            cs.get_cluster_members()

    def test_list_tokens(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": [
                {
                    "name": "node-2",
                    "token": "TESTTOKEN",
                },
            ],
        }

        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        tokens = cs.list_tokens()
        tokens_from_call = [t.get("token") for t in tokens]
        tokens_from_mock = [t.get("token") for t in json_data.get("metadata")]
        assert tokens_from_mock == tokens_from_call

    def test_delete_token(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.delete_token("node-2")

    def test_delete_token_when_token_doesnot_exists(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 404,
            "error": "InternalTokenRecord not found",
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.TokenNotFoundException):
            cs.delete_token("node-3")

    def test_remove(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.remove("node-2")

    def test_remove_when_node_doesnot_exist(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 404,
            "error": 'No remote exists with the given name "node-3"',
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.NodeNotExistInClusterException):
            cs.delete_token("node-3")

    def test_remove_when_node_is_last_member(self):
        json_data = {
            "type": "error",
            "status": "",
            "status_code": 0,
            "operation": "",
            "error_code": 404,
            "error": (
                "Cannot remove cluster members, there are no remaining "
                "non-pending members"
            ),
            "metadata": None,
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
            raise_for_status=HTTPError("Internal Error"),
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        with pytest.raises(service.LastNodeRemovalFromClusterException):
            cs.delete_token("node-3")

    def test_add_node_info(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.add_node_info("node-1", ["control"])

    def test_remove_node_info(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.remove_node_info("node-1")

    def test_list_nodes(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": [
                {
                    "name": "node-1",
                    "role": "control",
                    "machineid": 0,
                }
            ],
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        nodes = cs.list_nodes()
        nodes_from_call = [node.get("name") for node in nodes]
        nodes_from_mock = [node.get("name") for node in json_data.get("metadata")]
        assert nodes_from_mock == nodes_from_call

    def test_update_node_info(self):
        json_data = {
            "type": "sync",
            "status": "Success",
            "status_code": 200,
            "operation": "",
            "error_code": 0,
            "error": "",
            "metadata": {},
        }
        mock_response = self._mock_response(
            status=200,
            json_data=json_data,
        )

        mock_session = MagicMock()
        mock_session.request.return_value = mock_response

        cs = ClusterService(mock_session, "http+unix://mock")
        cs.update_node_info("node-2", ["control"], 2)


class TestClusterUpdateJujuControllerStep:
    """Unit tests for sunbeam clusterd steps."""

    def test_init_step(self):
        step = ClusterUpdateJujuControllerStep(MagicMock(), "10.0.0.10:10")
        assert step.filter_ips(["10.0.0.6:17070"], "10.0.0.0/24") == ["10.0.0.6:17070"]
        assert step.filter_ips(["10.10.0.6:17070"], "10.0.0.0/24") == [
            "10.10.0.6:17070"
        ]
        assert step.filter_ips(["10.10.0.6:17070"], "10.0.0.0/24,10.10.0.0/24") == [
            "10.10.0.6:17070"
        ]
        assert step.filter_ips(
            ["10.0.0.6:17070", "[fd42:5eda:f578:7bba:216:3eff:fe3d:7ef6]:17070"],
            "10.0.0.0/24",
        ) == ["10.0.0.6:17070"]

    def test_skip(self, cclient, snap, run, load_answers):
        controller_name = "lxdcloud"
        endpoints = ["10.0.0.1:17070", "[fd42:9331:57e6:2088:216:3eff:fe82:2bb6]:17070"]
        management_cidr = "10.0.0.0/24"

        controller = json.dumps(
            {controller_name: {"details": {"api-endpoints": endpoints}}}
        )
        run.return_value = subprocess.CompletedProcess(
            args={}, returncode=0, stdout=controller
        )

        load_answers.return_value = {"bootstrap": {"management_cidr": management_cidr}}
        cclient.cluster.get_config.side_effect = ConfigItemNotFoundException()

        step = ClusterUpdateJujuControllerStep(cclient, controller_name)
        result = step.is_skip()

        assert result.result_type == ResultType.COMPLETED

    def test_skip_when_controller_details_exist_in_clusterdb(
        self, cclient, snap, run, load_answers
    ):
        controller_name = "lxdcloud"
        endpoints = ["10.0.0.1:17070", "[fd42:9331:57e6:2088:216:3eff:fe82:2bb6]:17070"]
        management_cidr = "10.0.0.0/24"

        controller = json.dumps(
            {controller_name: {"details": {"api-endpoints": endpoints}}}
        )
        run.return_value = subprocess.CompletedProcess(
            args={}, returncode=0, stdout=controller
        )

        load_answers.return_value = {"bootstrap": {"management_cidr": management_cidr}}
        cclient.cluster.get_config.return_value = json.dumps(
            {
                "name": controller_name,
                "api_endpoints": [endpoints[0]],
                "ca_cert": "TMP_CA_CERT",
                "is_external": False,
            }
        )

        step = ClusterUpdateJujuControllerStep(cclient, controller_name)
        result = step.is_skip()

        assert result.result_type == ResultType.SKIPPED


@pytest.fixture()
def manifest():
    mock = Mock()
    mock.software_config.charms = {
        "sunbeam-clusterd": Mock(channel="my-channel", config={})
    }
    return mock


class TestDeploySunbeamClusterdApplicationStep:
    def test_is_skip_when_application_not_found(self, manifest, model):
        jhelper = Mock()
        jhelper.get_application.side_effect = ApplicationNotFoundException
        step = DeploySunbeamClusterdApplicationStep(jhelper, manifest, model)
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_is_skip_when_application_found(self, manifest, model):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock()
        step = DeploySunbeamClusterdApplicationStep(jhelper, manifest, model)
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_run_when_no_machines_found(self, manifest, model):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock()
        jhelper.get_machines.return_value = {}
        step = DeploySunbeamClusterdApplicationStep(jhelper, manifest, model)
        result = step.run()
        assert result.result_type == ResultType.FAILED
        assert result.message == f"No machines found in {model} model"

    def test_run_when_machines_found(self, manifest, model):
        jhelper = Mock()
        jhelper.get_application.return_value = Mock()
        jhelper.get_machines.return_value = {"1": "m1", "2": "m2", "3": "m3"}
        manifest.core.software.charms = {"sunbeam-clusterd": Mock(config={})}
        step = DeploySunbeamClusterdApplicationStep(jhelper, manifest, model)
        result = step.run()
        assert result.result_type == ResultType.COMPLETED


class TestSaveManagementCidrStep:
    def test_is_skip_when_management_cidr_already_saved(self):
        client = Mock()
        client.cluster.get_config.return_value = """{
            "bootstrap": {
                "management_cidr": "10.0.0.0/24"
            }
        }"""
        step = SaveManagementCidrStep(client, "10.0.0.0/24")
        result = step.is_skip()
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_when_management_cidr_not_saved(self):
        client = Mock()
        client.cluster.get_config.side_effect = service.ConfigItemNotFoundException
        step = SaveManagementCidrStep(client, "10.0.0.0/24")
        step.variables = {"bootstrap": {"management_cidr": "10.0.0.1/24"}}
        result = step.is_skip()
        assert result.result_type == ResultType.COMPLETED

    def test_run_successfully_saves_management_cidr(self):
        client = Mock()
        client.cluster.update_config.side_effect = lambda x, y: None
        step = SaveManagementCidrStep(client, "10.0.0.0/24")
        step.variables = {"bootstrap": {}}
        result = step.run()
        assert result.result_type == ResultType.COMPLETED

    def test_run_handles_cluster_service_unavailable_exception(self):
        client = Mock()
        client.cluster.update_config.side_effect = (
            service.ClusterServiceUnavailableException("Cluster service is unavailable")
        )
        step = SaveManagementCidrStep(client, "10.0.0.0/24")
        step.variables = {"bootstrap": {}}
        result = step.run()
        assert result.result_type == ResultType.FAILED
