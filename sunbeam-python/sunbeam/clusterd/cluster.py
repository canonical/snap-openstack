# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import secrets
from typing import Any, Union

from requests import codes
from requests.models import HTTPError

from sunbeam.clusterd import models, service

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
        self._post("/core/control", data=json.dumps(data))

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
        self._post("/core/control", data=json.dumps(data))

    def get_cluster_members(self) -> list:
        """List members in the cluster.

        Returns a list of all members in the cluster.
        """
        result = []
        cluster = self._get("/core/1.0/cluster")
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
        self._delete(f"/core/1.0/cluster/{name}")

    def generate_token(self, name: str) -> str:
        """Generate token for the node.

        Generate a new token for the node with name.

        Raises TokenAlreadyGeneratedException if token is already
        generated.
        """
        data = {"name": name}
        result = self._post("/core/control/tokens", data=json.dumps(data))
        return result.get("metadata")

    def list_tokens(self) -> list:
        """List all generated tokens."""
        tokens = self._get("/core/control/tokens")
        return tokens.get("metadata")

    def delete_token(self, name: str) -> None:
        """Delete token for the node.

        Raises TokenNotFoundException if token does not exist.
        """
        self._delete(f"/core/1.0/tokens/{name}")


class ExtendedAPIService(service.BaseService):
    """Client for Sunbeam extended Cluster API."""

    def add_node_info(
        self, name: str, role: list[str], machineid: int = -1, systemid: str = ""
    ) -> None:
        """Add Node information to cluster database."""
        data = {
            "name": name,
            "role": role,
            "machineid": machineid,
            "systemid": systemid,
        }
        self._post("/1.0/nodes", data=json.dumps(data))

    def list_nodes(self) -> list[dict]:
        """List all nodes."""
        nodes = self._get("/1.0/nodes")
        return nodes.get("metadata")

    def get_node_info(self, name: str) -> dict:
        """Fetch Node Information from a name."""
        return self._get(f"1.0/nodes/{name}").get("metadata")

    def remove_node_info(self, name: str) -> None:
        """Remove Node information from cluster database."""
        self._delete(f"1.0/nodes/{name}")

    def update_node_info(
        self,
        name: str,
        role: list[str] | None = None,
        machineid: int = -1,
        systemid: str = "",
    ) -> None:
        """Update role and machineid for node."""
        data = {"role": role, "machineid": machineid, "systemid": systemid}
        self._put(f"1.0/nodes/{name}", data=json.dumps(data))

    def add_juju_user(self, name: str, token: str) -> None:
        """Add juju user to cluster database."""
        data = {"username": name, "token": token}
        self._post("/1.0/jujuusers", data=json.dumps(data))

    def update_juju_user(self, name: str, token: str) -> None:
        """Update juju user in cluster database."""
        data = {"username": name, "token": token}
        try:
            self._put(f"/1.0/jujuusers/{name}", data=json.dumps(data))
        except HTTPError as e:
            if e.response is not None and e.response.status_code == codes.not_found:
                raise service.JujuUserNotFoundException(f"{name} not found") from e
            raise e

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

    def list_nodes_by_role(self, role: Union[str, list[str]]) -> list:
        """List nodes by role."""
        if isinstance(role, list):
            role = "&role=".join(role)
        nodes = self._get(f"/1.0/nodes?role={role}")
        return nodes.get("metadata")

    def list_terraform_plans(self) -> list[str]:
        """List all plans."""
        plans = self._get("/1.0/terraformstate")
        return plans.get("metadata")

    def list_terraform_locks(self) -> list[str]:
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

    def add_manifest(self, data: str) -> str:
        """Add manifest to cluster database."""
        manifest_id = secrets.token_hex(16)
        content = {"manifestid": manifest_id, "data": data}
        self._post("/1.0/manifests", data=json.dumps(content))
        return manifest_id

    def list_manifests(self) -> list:
        """List all manifests."""
        manifests = self._get("/1.0/manifests")
        return manifests.get("metadata")

    def get_manifest(self, manifest_id: str) -> dict:
        """Get manifest info along with data."""
        manifest = self._get(f"/1.0/manifests/{manifest_id}")
        return manifest.get("metadata")

    def get_latest_manifest(self) -> dict:
        """Get latest manifest."""
        return self.get_manifest("latest")

    def delete_manifest(self, manifest_id: str) -> None:
        """Remove manifest from database."""
        self._delete(f"/1.0/manifest/{manifest_id}")

    def get_server_certpair(self) -> dict:
        """Fetch server certpair from cluster.

        This will always raise a 403 exception if not used over
        the unix socket.
        """
        return self._get("/local/certpair/server", redact_response=True).get("metadata")

    def get_status(self) -> dict[str, dict]:
        """Get status of the cluster."""
        cluster = self._get("/1.0/status")
        members = cluster.get("metadata", {})
        return {
            member["name"]: {
                "status": member["status"],
                "address": member["address"],
            }
            for member in members
        }

    def get_storage_backends(self) -> models.StorageBackends:
        """List all storage backends."""
        backends = self._get("/1.0/storage-backend")
        return models.StorageBackends(root=backends.get("metadata", []))

    def get_storage_backend(self, name: str) -> models.StorageBackend:
        """Get storage backend by name."""
        backend = self._get(f"/1.0/storage-backend/{name}")
        return models.StorageBackend(**backend.get("metadata", {}))

    def add_storage_backend(
        self,
        name: str,
        backend_type: str,
        config: dict[str, Any],
        principal: str,
        model_uuid: str,
    ) -> None:
        """Add a new storage backend."""
        data = {
            "name": name,
            "type": backend_type,
            "config": json.dumps(config),
            "principal": principal,
            "model-uuid": model_uuid,
        }
        self._post("/1.0/storage-backend", data=json.dumps(data))

    def delete_storage_backend(self, name: str) -> None:
        """Delete storage backend by name."""
        self._delete(f"/1.0/storage-backend/{name}")

    def update_storage_backend(
        self,
        name: str,
        backend_type: str | None = None,
        config: dict[str, Any] | None = None,
        principal: str | None = None,
        model_uuid: str | None = None,
    ) -> None:
        """Update an existing storage backend."""
        data: dict[str, Any] = {}
        if backend_type is not None:
            data["type"] = backend_type
        if config is not None:
            data["config"] = json.dumps(config)
        if principal is not None:
            data["principal"] = principal
        if model_uuid is not None:
            data["model-uuid"] = model_uuid
        self._put(f"/1.0/storage-backend/{name}", data=json.dumps(data))


class ClusterService(MicroClusterService, ExtendedAPIService):
    """Lists and manages cluster."""

    # SUNBEAM_BOOTSTRAP_KEY is used to track whether sunbeam bootstrap has
    # sucessfully run. Note: this is distinct from microcluster bootstrap.
    SUNBEAM_BOOTSTRAP_KEY = "sunbeam_bootstrapped"

    # This key is used to determine if Juju controller is migrated to k8s
    # from lxd. This is used only in local type deployment.
    JUJU_CONTROLLER_MIGRATE_KEY = "juju_controller_migrated_to_k8s"

    def bootstrap(
        self, name: str, address: str, role: list[str], machineid: int = -1
    ) -> None:
        """Bootstrap cluster and register node information."""
        self.bootstrap_cluster(name, address)
        self.add_node_info(name, role, machineid)

    def add_node(self, name: str) -> str:
        """Request token for additional node."""
        return self.generate_token(name)

    def join_node(self, name: str, address: str, token: str, role: list[str]) -> None:
        """Join node to cluster and register node information."""
        self.join(name, address, token)
        self.add_node_info(name, role)

    def remove_node(self, name) -> None:
        """Remove node from cluster and database.

        If node is not part of cluster, remove its potential token.
        """
        members = self.get_cluster_members()
        member_names = [member.get("name") for member in members]

        # If node is part of cluster, remove node from cluster
        if name in member_names:
            # Cannot remove user as the same user name cannot be reused
            # self.remove_juju_user(name)
            self.remove_node_info(name)
            self.remove(name)
        else:
            # Check if token exists in token list and remove
            self.delete_token(name)

    def unset_sunbeam_bootstrapped(self) -> None:
        """Remove sunbeam bootstrapped key."""
        self.update_config(self.SUNBEAM_BOOTSTRAP_KEY, json.dumps("False"))

    def set_sunbeam_bootstrapped(self) -> None:
        """Mark sunbeam deployment as bootstrapped."""
        self.update_config(self.SUNBEAM_BOOTSTRAP_KEY, json.dumps("True"))

    def check_sunbeam_bootstrapped(self) -> bool:
        """Check if the sunbeam deployment has been bootstrapped."""
        try:
            state = json.loads(self.get_config(self.SUNBEAM_BOOTSTRAP_KEY))
        except service.ConfigItemNotFoundException:
            state = False
        except service.ClusterServiceUnavailableException:
            state = False
        return state

    def unset_juju_controller_migrated(self) -> None:
        """Remove juju controller migrated key."""
        self.update_config(self.JUJU_CONTROLLER_MIGRATE_KEY, json.dumps("False"))

    def set_juju_controller_migrated(self) -> None:
        """Mark juju controller as migrated."""
        self.update_config(self.JUJU_CONTROLLER_MIGRATE_KEY, json.dumps("True"))

    def check_juju_controller_migrated(self) -> bool:
        """Check if juju controller has been migrated."""
        try:
            state = json.loads(self.get_config(self.JUJU_CONTROLLER_MIGRATE_KEY))
        except service.ConfigItemNotFoundException:
            state = False
        except service.ClusterServiceUnavailableException:
            state = False
        return state
