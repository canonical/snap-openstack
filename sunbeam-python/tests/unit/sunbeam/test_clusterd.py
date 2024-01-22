# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, Mock

import pytest
from requests.exceptions import HTTPError

import sunbeam.clusterd.service as service
from sunbeam.clusterd.cluster import ClusterService
from sunbeam.commands.clusterd import (
    ClusterAddJujuUserStep,
    ClusterAddNodeStep,
    ClusterInitStep,
    ClusterJoinNodeStep,
    ClusterListNodeStep,
    ClusterRemoveNodeStep,
    ClusterUpdateJujuControllerStep,
    ClusterUpdateNodeStep,
)
from sunbeam.jobs.common import ResultType


@pytest.fixture()
def cclient():
    yield Mock()


class TestClusterdSteps:
    """Unit tests for sunbeam clusterd steps."""

    def test_init_step(self, cclient):
        role = "control"
        init_step = ClusterInitStep(cclient, [role])
        init_step.client = MagicMock()
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
            cclient, token="TESTTOKEN", role=["control"]
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
            "error": "Daemon not yet initialized",
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
        cs.add_node_info("node-1", "control")

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
        cs.update_node_info("node-2", "control", "2")


class TestClusterUpdateJujuControllerStep:
    """Unit tests for sunbeam clusterd steps."""

    def test_init_step(self):
        step = ClusterUpdateJujuControllerStep(MagicMock(), "10.0.0.10:10")
        assert step.filter_ips(["10.0.0.6:17070"], "10.0.0.0/24") == ["10.0.0.6:17070"]
        assert step.filter_ips(["10.10.0.6:17070"], "10.0.0.0/24") == []
        assert step.filter_ips(["10.10.0.6:17070"], "10.0.0.0/24,10.10.0.0/24") == [
            "10.10.0.6:17070"
        ]
        assert step.filter_ips(
            ["10.0.0.6:17070", "[fd42:5eda:f578:7bba:216:3eff:fe3d:7ef6]:17070"],
            "10.0.0.0/24",
        ) == ["10.0.0.6:17070"]
