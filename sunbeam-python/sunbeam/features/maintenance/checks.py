# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import typing
from collections.abc import Mapping

from rich.console import Console

from sunbeam.clusterd.client import Client
from sunbeam.core.checks import Check
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ActionFailedException,
    ApplicationNotFoundException,
    ExecFailedException,
    JujuActionHelper,
    JujuException,
    JujuHelper,
    UnitNotFoundException,
)
from sunbeam.core.k8s import (
    K8S_APP_NAME,
    K8S_DATASTORE_CONFIG,
    K8S_DEFAULT_JUJU_CONTROLLER_NAMESPACE,
    K8S_DQLITE_SVC_NAME,
    fetch_pods,
    fetch_pods_for_eviction,
    find_node,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.openstack_api import (
    get_admin_connection,
    guests_on_hypervisor,
)
from sunbeam.core.watcher import WATCHER_APPLICATION
from sunbeam.lazy import LazyImport
from sunbeam.provider.maas.deployment import is_maas_deployment
from sunbeam.steps.k8s import (
    K8SError,
    K8SNodeNotFoundError,
    KubeClientError,
    get_kube_client,
)

if typing.TYPE_CHECKING:
    import lightkube.core.client as l_client
    import lightkube.resources.apps_v1 as l_apps
else:
    l_client = LazyImport("lightkube.core.client")
    l_apps = LazyImport("lightkube.resources.apps_v1")
from sunbeam.steps.microceph import APPLICATION as _MICROCEPH_APPLICATION

console = Console()
LOG = logging.getLogger(__name__)
COMMAND_TIMEOUT = 60


class InstancesStatusCheck(Check):
    """Detect if any instance in unexpected status.

    - If there are any instance in ERROR status,
        operator should manually handle it first.
    - If there are any instance in MIGRATING status,
        operator should wait until migration finished.
    - If there are any instance in SHUTOFF status,
        the maintenance will be blocked, because sunbeam doesn't support
        cold migration now. this will blocked until we have disable cold migration
        feature support in watcher. see:
        https://bugs.launchpad.net/snap-openstack/+bug/2082056 and
        https://review.opendev.org/c/openstack/watcher-specs/+/943873
    """

    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance in ERROR/MIGRATING/SHUTOFF status on nodes",
            (
                "Checking if there are any instance in"
                " ERROR/MIGRATING/SHUTOFF status on nodes"
            ),
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        not_expected_status_instances: dict[str, str] = {}

        for status in ["ERROR", "MIGRATING", "SHUTOFF"]:
            for inst in guests_on_hypervisor(
                hypervisor_name=self.node,
                conn=conn,
                status=status,
            ):
                not_expected_status_instances[inst.id] = status

        if not_expected_status_instances:
            _msg = f"Instances not in expected status: {not_expected_status_instances}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NoEphemeralDiskCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance using ephemeral disk",
            "Checking if there are any instance is using ephemeral disk",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        unexpected_instances = []

        for inst in guests_on_hypervisor(
            hypervisor_name=self.node,
            conn=conn,
        ):
            flavor = conn.compute.find_flavor(inst.flavor.get("id"))
            if flavor.ephemeral > 0:
                unexpected_instances.append(inst.id)
        if unexpected_instances:
            _msg = f"Instances have ephemeral disk: {unexpected_instances}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NoInstancesOnNodeCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check no instance on the node",
            "Check no instance on the node",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        instances = guests_on_hypervisor(hypervisor_name=self.node, conn=conn)

        if len(instances) > 0:
            instance_ids = ",".join([inst.id for inst in instances])
            _msg = f"Instances {instance_ids} still on node {self.node}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class NovaInDisableStatusCheck(Check):
    def __init__(self, jhelper: JujuHelper, node: str, force: bool):
        super().__init__(
            "Check nova compute is disable on the node",
            "Check nova compute is disable on the node",
        )
        self.jhelper = jhelper
        self.node = node
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        conn = get_admin_connection(jhelper=self.jhelper)

        expected_services = []
        for svc in conn.compute.services(
            binary="nova-compute", host=self.node, status="disabled"
        ):
            expected_services.append(svc.id)

        if not len(expected_services) == 1:
            _msg = f"Nova compute still not disabled on node {self.node}"
            if self.force:
                LOG.warning(f"Ignore issue: {_msg}")
                return True
            self.message = _msg
            return False
        return True


class MicroCephMaintenancePreflightCheck(Check):
    def __init__(
        self,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        node: str,
        action_params: dict[str, typing.Any],
        force: bool,
    ):
        super().__init__(
            "Run MicroCeph enter maintenance preflight checks",
            "Run MicroCeph enter maintenance preflight checks",
        )
        self.client = client
        self.node = node
        self.jhelper = jhelper
        self.model = model
        self.action_params = action_params
        self.action_params["dry-run"] = False
        self.action_params["check-only"] = True
        self.force = force

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        try:
            JujuActionHelper.run_action(
                client=self.client,
                jhelper=self.jhelper,
                model=self.model,
                node=self.node,
                app=_MICROCEPH_APPLICATION,
                action_name="enter-maintenance",
                action_params=self.action_params,
            )
        except UnitNotFoundException:
            self.message = (
                f"App {_MICROCEPH_APPLICATION} unit not found on node {self.node}"
            )
            return False
        except ActionFailedException as e:
            for _, action in e.action_result.get("actions", {}).items():
                if action.get("error"):
                    msg = action.get("error")
                    if self.force:
                        LOG.warning(f"Ignore issue: {msg}")
                    else:
                        self.message = msg
                        return False
        return True


class WatcherApplicationExistsCheck(Check):
    """Make sure watcher application exists in model."""

    def __init__(
        self,
        jhelper: JujuHelper,
    ):
        super().__init__(
            "Check if watcher is deployed.",
            "Check if watcher is deployed.",
        )
        self.jhelper = jhelper

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        try:
            self.jhelper.get_application(
                name=WATCHER_APPLICATION,
                model=OPENSTACK_MODEL,
            )
        except ApplicationNotFoundException:
            self.message = (
                "Watcher not found, please deploy watcher with command:"
                " `sunbeam enable resource-optimization` before continue"
            )
            return False
        return True


class NodeExistCheck(Check):
    """Check if the node is in the cluster."""

    def __init__(self, node: str, cluster_status: dict[str, typing.Any]):
        super().__init__(
            "Check if the node is in the cluster.",
            "Checking if the node is in the cluster.",
        )
        self.node = node
        self.cluster_status = cluster_status

    def run(self) -> bool:
        """Check if the node is in the cluster."""
        if not self.cluster_status.get(self.node):
            self.message = f"'{self.node}' does not exist in cluster."
            return False
        return True


class NoLastNodeCheck(Check):
    """Check if the cluster has more than one node."""

    def __init__(self, cluster_status: dict[str, typing.Any], force: bool = False):
        super().__init__(
            "Check if the cluster has more than one node.",
            "Checking if the cluster has more than one node.",
        )
        self.force = force
        self.cluster_status = cluster_status

    def run(self) -> bool:
        """Check if the cluster has more than one node."""
        if len(self.cluster_status) > 1:
            return True

        if self.force:
            LOG.debug("Ignore issue: only one node error")
            return True

        self.message = (
            "cannot enable maintenance mode because there is only one node in the"
            " cluster."
        )
        return False


class NoLastControlRoleCheck(Check):
    """Check if the cluster has more than one node with active control role."""

    def __init__(
        self,
        deployment: Deployment,
        cluster_status: dict[str, typing.Any],
        force: bool = False,
    ):
        super().__init__(
            "Check if the cluster has more than one node with active control role.",
            "Checking if the cluster has more than one node with active control role.",
        )
        self.force = force
        self.deployment = deployment
        self.cluster_status = cluster_status

    def run(self) -> bool:
        """Check if the cluster has only one active control role."""
        try:
            kube_client = get_kube_client(self.deployment.get_client())
        except KubeClientError:
            self.message = "failed to get k8s client"
            return False

        num_active_control_roles = 0
        for node, status in self.cluster_status.items():
            if "control" not in status:
                LOG.debug("%s is not an control role node", node)
                continue
            if not self._is_active_control_role_node(node, kube_client):
                LOG.debug("%s is not an active control role node", node)
                continue
            LOG.debug("%s is an active control role node", node)
            num_active_control_roles += 1

        if num_active_control_roles > 1:
            return True

        if self.force:
            LOG.debug("Ignore issue: only one active control role error")
            return True

        self.message = (
            "cannot enable maintenance mode because there is only one node with"
            " active control role in the cluster."
        )
        return False

    def _is_active_control_role_node(
        self, name: str, kube_client: "l_client.Client"
    ) -> bool:
        """Check if the node is a node with active control role.

        Current, a node with "active" control role is defined as a node with control
        role (or a k8s node) and is uncordoned (or not unschedulable).
        """
        try:
            node = find_node(kube_client, name)
        except (K8SNodeNotFoundError, K8SError):
            return False
        if node.spec and not node.spec.unschedulable:
            return True
        return False


class K8sDqliteRedundancyCheck(Check):
    """Check if the k8s dqlite has enough redundancy."""

    def __init__(
        self,
        node: str,
        jhelper: JujuHelper,
        deployment: Deployment,
        force: bool = False,
    ):
        super().__init__(
            "Check if the k8s dqlite has enough redundancy.",
            "Checking if the k8s dqlite has enough redundancy.",
        )
        self.node = node
        self.force = force
        self.jhelper = jhelper
        self.deployment = deployment

    def run(self) -> bool:
        """Check if the k8s dqlite has enough redundancy."""
        datastore = self._get_k8s_configs().get(K8S_DATASTORE_CONFIG, "")
        if datastore != "dqlite":
            LOG.debug(
                "Skipped checking %s redundancy: %s bootstrap datastore is not dqlte",
                K8S_DQLITE_SVC_NAME,
                K8S_APP_NAME,
            )
            return True

        this_k8s_unit_name = self._get_this_k8s_unit_name(self.node)

        total_k8s_dqlite_svcs = 0
        remaining_active_k8s_dqlite_svcs = 0
        for k8s_unit_name in self._get_k8s_unit_names():
            total_k8s_dqlite_svcs += 1
            if this_k8s_unit_name == k8s_unit_name:
                continue  # don't count this node
            if self._has_active_k8s_dqlite_svc(k8s_unit_name):
                remaining_active_k8s_dqlite_svcs += 1

        min_k8s_dqlite_svcs = total_k8s_dqlite_svcs // 2 + 1

        if remaining_active_k8s_dqlite_svcs >= min_k8s_dqlite_svcs:
            return True

        if self.force:
            LOG.debug("Ignore issue: not enough %s error", K8S_DQLITE_SVC_NAME)
            return True

        self.message = (
            "cannot enable maintenance mode because there is not enough"
            f" {K8S_DQLITE_SVC_NAME} in the cluster to maintain quorom"
            f" (want {min_k8s_dqlite_svcs} for a {total_k8s_dqlite_svcs} node k8s"
            f" cluster, but will have {remaining_active_k8s_dqlite_svcs} left after"
            " enabling maintenance mode for this node)."
        )

        return False

    def _get_k8s_configs(self) -> Mapping:
        """Return the config of all k8s app."""
        try:
            config = self.jhelper.get_app_config(
                K8S_APP_NAME, self.deployment.openstack_machines_model
            )
            LOG.debug("Got config %s for %s", config, K8S_APP_NAME)
        except (ApplicationNotFoundException, JujuException) as e:
            LOG.debug("%s", str(e))
            return {}
        return config

    def _get_k8s_unit_names(self) -> list[str]:
        """Return the names of all k8s units."""
        try:
            k8s_application = self.jhelper.get_application(
                K8S_APP_NAME, self.deployment.openstack_machines_model
            )
            LOG.debug("Got %s application", k8s_application)
        except ApplicationNotFoundException as e:
            LOG.debug("%s", str(e))
            return []
        return list(k8s_application.units.keys())

    def _get_this_k8s_unit_name(self, node: str) -> str:
        """Return the name of the k8s unit in this node."""
        try:
            return JujuActionHelper.get_unit(
                self.deployment.get_client(),
                self.jhelper,
                self.deployment.openstack_machines_model,
                node,
                K8S_APP_NAME,
            )
        except UnitNotFoundException:
            LOG.debug("cannot find %s unit in '%s'", K8S_APP_NAME, node)
            return ""

    def _has_active_k8s_dqlite_svc(self, k8s_unit_name: str) -> bool:
        """Check if the k8s unit has active k8s dqlite svc."""
        try:
            result = self.jhelper.run_cmd_on_machine_unit_payload(
                k8s_unit_name,
                self.deployment.openstack_machines_model,
                f"snap services {K8S_DQLITE_SVC_NAME} | grep active",
                timeout=COMMAND_TIMEOUT,
            )
            LOG.debug(result)
            status = result.stdout.split()[2]
        except (ExecFailedException, IndexError) as e:
            LOG.debug("%s", str(e))
            return False
        return "active" == status


class ReplicasRedundancyCheck(Check):
    """Check if the k8s resource has enough replicas."""

    def __init__(
        self,
        node: str,
        deployment: Deployment,
        force: bool = False,
    ):
        super().__init__(
            "Check if the k8s resource enough replicas.",
            "Checking if the k8s resource has enough replicas.",
        )
        self.node = node
        self.force = force
        self.deployment = deployment

    def run(self) -> bool:
        """Check if the k8s resource has enough replicas."""
        try:
            kube_client = get_kube_client(self.deployment.get_client())
        except KubeClientError:
            self.message = "failed to get k8s client"
            return False

        resources = []
        for pod in fetch_pods_for_eviction(kube_client, self.node):
            if pod.metadata is None:
                continue
            namespace = pod.metadata.namespace or "*"
            for owner in pod.metadata.ownerReferences or []:
                if owner.kind == "ReplicaSet":
                    kind = l_apps.ReplicaSet  # type: ignore
                elif owner.kind == "Deployment":
                    kind = l_apps.Deployment  # type: ignore
                elif owner.kind == "StatefulSet":
                    kind = l_apps.StatefulSet  # type: ignore
                else:
                    continue

                r = kube_client.get(kind, owner.name, namespace=namespace)
                if not (r.spec and r.status):
                    continue
                desired_replicas = r.spec.replicas or 1
                ready_replicas = r.status.readyReplicas or 0
                if ready_replicas <= 1:
                    LOG.debug(
                        "name=%s kind=%s namespace=%s replicas=%d/%d",
                        owner.name,
                        owner.kind,
                        namespace,
                        ready_replicas,
                        desired_replicas,
                    )
                    resources.append(
                        f"- name={owner.name} kind={owner.kind} namespace={namespace}"
                        f" replicas={ready_replicas}/{desired_replicas}"
                    )

        if not resources:
            return True

        if self.force:
            LOG.debug("Ignore issue: k8s resource does not have enough replicas")
            return True

        self.message = (
            "cannot enable maintenance mode because the following resources have less"
            " than or equal to one replica:\n{}\n".format("\n".join(sorted(resources)))
            + "Deleting the pods from those resources may result in service downtime."
            " If you are sure, pass --allow-downtime to confirm the deletion."
        )
        return False


class NoJujuControllerPodCheck(Check):
    """Check if the node has juju controller pods."""

    def __init__(self, node: str, deployment: Deployment):
        super().__init__(
            "Check if there are juju contoller pods in the node.",
            "Checking if there are juju contoller pods in the node.",
        )
        self.node = node
        self.deployment = deployment

    def run(self) -> bool:
        """Check if the node has juju controller pods."""
        if is_maas_deployment(self.deployment):
            LOG.debug(
                "Skipped checking juju controller pods. MAAS deployment have"
                " juju-controller role instead of control role."
            )
            return True

        juju_controller = self.deployment.juju_controller
        if juju_controller and juju_controller.is_external:
            LOG.debug(
                "Skipped checking juju controller pods. Juju controller pod is not"
                " managed by Sunbeam for manual bare metal provider with an external."
            )
            return True

        try:
            juju_controller_pods = self._fetch_juju_controller_pods()
        except KubeClientError:
            self.message = "Failed to get k8s client"
            return False

        has_juju_controller_pods = False
        for pod in juju_controller_pods:
            # Found juju controller pod in this node
            if pod.spec and pod.spec.nodeName == self.node:
                LOG.debug(
                    "Found juju controller pod '%s' in '%s'",
                    pod.metadata.name,
                    self.node,
                )
                has_juju_controller_pods = True

        if not has_juju_controller_pods:
            return True

        self.message = (
            f"cannot enable maintenance mode for '{self.node}' because this"
            " node hosts juju controller pods."
        )
        return False

    def _is_juju_controller_pod(self, pod) -> bool:
        """Check if the pod is a juju controller pod or not."""
        return "controller" in pod.metadata.name

    def _fetch_juju_controller_pods(self) -> list:
        """Fetch juju controller pods in the default k8s juju controller namespace."""
        kube_client = get_kube_client(
            self.deployment.get_client(),
            K8S_DEFAULT_JUJU_CONTROLLER_NAMESPACE,
        )
        pods = fetch_pods(kube_client, K8S_DEFAULT_JUJU_CONTROLLER_NAMESPACE)
        return list(filter(self._is_juju_controller_pod, pods))


class ControlRoleNodeCordonedCheck(Check):
    """Check if the node is cordoned."""

    def __init__(self, node: str, deployment: Deployment, force: bool = False):
        super().__init__(
            "Check if the node is cordoned.",
            "Checking if the node is cordoned.",
        )
        self.node = node
        self.force = force
        self.deployment = deployment

    def run(self) -> bool:
        """Check if the node is cordoned."""
        try:
            kube_client = get_kube_client(self.deployment.get_client())
        except KubeClientError:
            self.message = "failed to get k8s client"
            return False

        try:
            node = find_node(kube_client, self.node)
        except (K8SNodeNotFoundError, K8SError):
            self.message = f"failed to get k8s node: '{self.node}'"
            return False

        if node.spec and node.spec.unschedulable:
            return True

        if self.force:
            LOG.debug("Ignore issue: node is not cordoned.")
            return True

        self.message = "node is not cordoned."

        return False


class ControlRoleNodeUncordonedCheck(Check):
    """Check if the node is uncordoned."""

    def __init__(self, node: str, deployment: Deployment, force: bool = False):
        super().__init__(
            "Check if the node is uncordoned.",
            "Checking if the node is uncordoned.",
        )
        self.node = node
        self.force = force
        self.deployment = deployment

    def run(self) -> bool:
        """Check if the node is uncordoned."""
        try:
            kube_client = get_kube_client(self.deployment.get_client())
        except KubeClientError:
            self.message = "failed to get k8s client"
            return False

        try:
            node = find_node(kube_client, self.node)
        except (K8SNodeNotFoundError, K8SError):
            self.message = f"failed to get k8s node: '{self.node}'"
            return False

        if node.spec and not node.spec.unschedulable:
            return True

        if self.force:
            LOG.debug("Ignore issue: node is not uncordoned.")
            return True

        self.message = "node is not uncordoned."

        return False
