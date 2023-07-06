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

import json
import logging
from typing import Any, List, Optional, Union

from requests import codes
from requests.models import HTTPError

from sunbeam.clusterd import service

LOG = logging.getLogger(__name__)


class MicroClusterService(service.BaseService):
    """Client for default MicroCluster Service API."""

    def bootstrap_cluster(self, name: str, address: str) -> None:
        """Bootstrap the micro cluster.

        Boostraps the cluster adding local node specified by
        name as bootstrap node. The address should be in
        format <IP>:<PORT> where the microcluster service
        is running.

        Raises NodeAlreadyExistsException if bootstrap is
        invoked on already existing node in cluster.
        """
        data = {"bootstrap": True, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def join(self, name: str, address: str, token: str) -> None:
        """Join node to the micro cluster.

        Verified the token with the list of saved tokens and
        joins the node with the given name and address.

        Raises NodeAlreadyExistsException if the node is already
        part of the cluster.
        Raises NodeJoinException if the token doesnot match or not
        part of the generated tokens list.
        """
        data = {"join_token": token, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def get_cluster_members(self) -> list:
        """List members in the cluster.

        Returns a list of all members in the cluster.
        """
        result = []
        cluster = self._get("/cluster/1.0/cluster")
        members = cluster.get("metadata", {})
        keys = ["name", "address", "status"]
        for member in members:
            result.append({k: v for k, v in member.items() if k in keys})
        return result

    def remove(self, name: str) -> None:
        """Remove node from the cluster.

        Raises NodeNotExistInClusterException if node does not
        exist in the cluster.
        Raises NodeRemoveFromClusterException if the node is last
        member of the cluster.
        """
        self._delete(f"/cluster/1.0/cluster/{name}")

    def generate_token(self, name: str) -> str:
        """Generate token for the node.

        Generate a new token for the node with name.

        Raises TokenAlreadyGeneratedException if token is already
        generated.
        """
        data = {"name": name}
        result = self._post("/cluster/1.0/tokens", data=json.dumps(data))
        return result.get("metadata")

    def list_tokens(self) -> list:
        """List all generated tokens."""
        tokens = self._get("/cluster/1.0/tokens")
        return tokens.get("metadata")

    def delete_token(self, name: str) -> None:
        """Delete token for the node.

        Raises TokenNotFoundException if token does not exist.
        """
        self._delete(f"/cluster/internal/tokens/{name}")


class ExtendedAPIService(service.BaseService):
    """Client for Sunbeam extended Cluster API."""

    def add_node_info(self, name: str, role: List[str]) -> None:
        """Add Node information to cluster database."""
        data = {"name": name, "role": role}
        self._post("/1.0/nodes", data=json.dumps(data))

    def list_nodes(self) -> list:
        """List all nodes."""
        nodes = self._get("/1.0/nodes")
        return nodes.get("metadata")

    def get_node_info(self, name: str) -> dict:
        """Fetch Node Information from a name"""
        return self._get(f"1.0/nodes/{name}").get("metadata")

    def remove_node_info(self, name: str) -> None:
        """Remove Node information from cluster database."""
        self._delete(f"1.0/nodes/{name}")

    def update_node_info(
        self, name: str, role: Optional[List[str]] = None, machineid: int = -1
    ) -> None:
        """Update role and machineid for node."""
        data = {"role": role, "machineid": machineid}
        self._put(f"1.0/nodes/{name}", data=json.dumps(data))

    def add_juju_user(self, name: str, token: str) -> None:
        """Add juju user to cluster database."""
        data = {"username": name, "token": token}
        self._post("/1.0/jujuusers", data=json.dumps(data))

    def list_juju_users(self) -> list:
        """List all juju users."""
        users = self._get("/1.0/jujuusers")
        return users.get("metadata")

    def remove_juju_user(self, name: str) -> None:
        """Remove Juju user from cluster database."""
        self._delete(f"1.0/jujuusers/{name}")

    def get_juju_user(self, name: str) -> dict:
        """Get Juju user from cluster database."""
        try:
            user = self._get(f"/1.0/jujuusers/{name}")
        except HTTPError as e:
            if e.response.status_code == codes.not_found:
                raise service.JujuUserNotFoundException()
            raise e
        return user.get("metadata")

    def get_config(self, key: str) -> Any:
        """Fetch configuration from database."""
        return self._get(f"/1.0/config/{key}").get("metadata")

    def update_config(self, key: str, value: Any):
        """Update configuration in database, create if missing."""
        self._put(f"/1.0/config/{key}", data=value)

    def delete_config(self, key: str):
        """Remove configuration from database."""
        self._delete(f"/1.0/config/{key}")

    def list_nodes_by_role(self, role: Union[str, List[str]]) -> list:
        """List nodes by role."""
        if isinstance(role, list):
            role = "&role=".join(role)
        nodes = self._get(f"/1.0/nodes?role={role}")
        return nodes.get("metadata")

    def list_terraform_plans(self) -> List[str]:
        """List all plans."""
        plans = self._get("/1.0/terraformstate")
        return plans.get("metadata")

    def list_terraform_locks(self) -> List[str]:
        """List all locks."""
        locks = self._get("/1.0/terraformlock")
        return locks.get("metadata")

    def get_terraform_lock(self, plan: str) -> dict:
        """Get lock information for plan."""
        lock = self._get(f"/1.0/terraformlock/{plan}")
        return json.loads(lock)

    def unlock_terraform_plan(self, plan: str, lock: dict) -> None:
        """Unlock plan."""
        self._put(f"/1.0/terraformunlock/{plan}", data=json.dumps(lock))


class ClusterService(MicroClusterService, ExtendedAPIService):
    """Lists and manages cluster."""

    def bootstrap(self, name: str, address: str, role: List[str]) -> None:
        self.bootstrap_cluster(name, address)
        self.add_node_info(name, role)

    def add_node(self, name: str) -> str:
        return self.generate_token(name)

    def join_node(self, name: str, address: str, token: str, role: List[str]) -> None:
        self.join(name, address, token)
        self.add_node_info(name, role)

    def remove_node(self, name) -> None:
        members = self.get_cluster_members()
        member_names = [member.get("name") for member in members]

        # If node is part of cluster, remove node from cluster
        if name in member_names:
            self.remove_juju_user(name)
            self.remove_node_info(name)
            self.remove(name)
        else:
            # Check if token exists in token list and remove
            self.delete_token(name)
