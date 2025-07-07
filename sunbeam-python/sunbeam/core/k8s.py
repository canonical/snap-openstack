# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import TYPE_CHECKING, Type

from snaphelpers import Snap  # noqa: F401 - required for test mocks

from sunbeam.core.common import SunbeamException
from sunbeam.lazy import LazyImport

if TYPE_CHECKING:
    import lightkube.core.client as l_client
    import lightkube.core.exceptions as l_exceptions
    import lightkube.generic_resource as l_generic_resource
    from lightkube import types as l_types
    from lightkube.models import meta_v1
    from lightkube.resources import core_v1
else:
    l_client = LazyImport("lightkube.core.client")
    l_exceptions = LazyImport("lightkube.core.exceptions")
    l_types = LazyImport("lightkube.types")
    l_generic_resource = LazyImport("lightkube.generic_resource")
    meta_v1 = LazyImport("lightkube.models.meta_v1")
    core_v1 = LazyImport("lightkube.resources.core_v1")


LOG = logging.getLogger(__name__)

# --- K8s specific
K8S_DEFAULT_STORAGECLASS = "csi-rawfile-default"
K8S_KUBECONFIG_KEY = "K8SKubeConfig"
SERVICE_LB_ANNOTATION = "io.cilium/lb-ipam-ips"

# --- Metallb specific
METALLB_IP_ANNOTATION = "metallb.io/loadBalancerIPs"
METALLB_ADDRESS_POOL_ANNOTATION = "metallb.io/address-pool"
METALLB_ALLOCATED_POOL_ANNOTATION = "metallb.io/ip-allocated-from-pool"
METALLB_INTERNAL_POOL_NAME = "metallb-loadbalancer-ck-loadbalancer"

CREDENTIAL_SUFFIX = "-creds"
K8S_CLOUD_SUFFIX = "-k8s"
LOADBALANCER_QUESTION_DESCRIPTION = """\
OpenStack services are exposed via virtual IP addresses.\
 This range should contain at least ten addresses\
 and must not overlap with external network CIDR.\
 To access APIs from a remote host, the range must reside\
 within the subnet that the primary network interface is on.\

On multi-node deployments, the range must be addressable from\
 all nodes in the deployment.\
"""
DEPLOYMENT_LABEL = "sunbeam/deployment"
HOSTNAME_LABEL = "sunbeam/hostname"


class K8SError(SunbeamException):
    """Common K8S error class."""


class K8SNodeNotFoundError(K8SError):
    """Node not found error."""


class K8SHelper:
    """K8S Helper that provides cloud constants."""

    @classmethod
    def get_provider(cls) -> str:
        """Return k8s provider from snap settings."""
        return "k8s"

    @classmethod
    def get_cloud(cls, deployment_name: str) -> str:
        """Return cloud name matching provider."""
        return f"{deployment_name}{K8S_CLOUD_SUFFIX}"

    @classmethod
    def get_default_storageclass(cls) -> str:
        """Return storageclass matching provider."""
        return K8S_DEFAULT_STORAGECLASS

    @classmethod
    def get_kubeconfig_key(cls) -> str:
        """Return kubeconfig key matching provider."""
        return K8S_KUBECONFIG_KEY

    @classmethod
    def get_loadbalancer_ip_annotation(cls) -> str:
        """Return loadbalancer ip annotation matching provider."""
        return METALLB_IP_ANNOTATION

    @classmethod
    def get_loadbalancer_address_pool_annotation(cls) -> str:
        """Return loadbalancer address pool annotation matching provider."""
        return METALLB_ADDRESS_POOL_ANNOTATION

    @classmethod
    def get_loadbalancer_allocated_pool_annotation(cls) -> str:
        """Return loadbalancer allocated ip pool annotation."""
        return METALLB_ALLOCATED_POOL_ANNOTATION

    @classmethod
    def get_lightkube_loadbalancer_resource(
        cls,
    ) -> Type["l_generic_resource.GenericNamespacedResource"]:
        """Return lighkube generic resource of type loadbalancer."""
        return l_generic_resource.create_namespaced_resource(
            "metallb.io",
            "v1beta1",
            "IPAddressPool",
            "ipaddresspools",
            verbs=["delete", "get", "list", "patch", "post", "put"],
        )

    @classmethod
    def get_lightkube_l2_advertisement_resource(
        cls,
    ) -> Type["l_generic_resource.GenericNamespacedResource"]:
        """Return lighkube generic resource of type l2advertisement."""
        return l_generic_resource.create_namespaced_resource(
            "metallb.io",
            "v1beta1",
            "L2Advertisement",
            "l2advertisements",
            verbs=["delete", "get", "list", "patch", "post", "put"],
        )

    @classmethod
    def get_loadbalancer_namespace(cls) -> str:
        """Return namespace for loadbalancer."""
        return "metallb-system"

    @classmethod
    def get_internal_pool_name(cls) -> str:
        """Return internal pool name."""
        return METALLB_INTERNAL_POOL_NAME


def find_node(client: "l_client.Client", name: str) -> "core_v1.Node":
    """Find a node by name."""
    try:
        return client.get(core_v1.Node, name)
    except l_exceptions.ApiError as e:
        if e.status.code == 404:
            raise K8SNodeNotFoundError(f"Node {name} not found")
        raise K8SError(f"Failed to get node {name}") from e


def cordon(client: "l_client.Client", name: str):
    """Taint a node as unschedulable."""
    LOG.debug("Marking %s unschedulable", name)
    try:
        client.patch(core_v1.Node, name, {"spec": {"unschedulable": True}})
    except l_exceptions.ApiError as e:
        if e.status.code == 404:
            raise K8SNodeNotFoundError(f"Node {name} not found")
        raise K8SError(f"Failed to patch node {name}") from e


def is_not_daemonset(pod):
    return pod.metadata.ownerReferences[0].kind != "DaemonSet"


def fetch_pods(
    client: "l_client.Client",
    namespace: str | None = None,
    labels: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
) -> list["core_v1.Pod"]:
    """Fetch all pods on node that can be evicted.

    DaemonSet pods cannot be evicted as they don't respect unschedulable flag.
    """
    return list(
        client.list(
            res=core_v1.Pod,
            namespace=namespace if namespace else "*",
            labels=labels,  # type: ignore
            fields=fields,  # type: ignore
        )
    )


def fetch_pods_for_eviction(
    client: "l_client.Client", node_name: str, labels: dict[str, str] | None = None
) -> list["core_v1.Pod"]:
    """Fetch all pods on node that can be evicted.

    DaemonSet pods cannot be evicted as they don't respect unschedulable flag.
    """
    pods = fetch_pods(
        client,
        labels=labels,
        fields={"spec.nodeName": node_name},
    )
    return list(filter(is_not_daemonset, pods))


def evict_pods(client: "l_client.Client", pods: list["core_v1.Pod"]) -> None:
    for pod in pods:
        if pod.metadata is None:
            continue
        LOG.debug(f"Evicting pod {pod.metadata.name}")
        evict = core_v1.Pod.Eviction(
            metadata=meta_v1.ObjectMeta(
                name=pod.metadata.name, namespace=pod.metadata.namespace
            ),
        )
        client.create(evict, name=str(pod.metadata.name))


def fetch_pvc(
    client: "l_client.Client", pods: list["core_v1.Pod"]
) -> list["core_v1.PersistentVolumeClaim"]:
    pvc = []
    for pod in pods:
        if pod.spec is None or pod.spec.volumes is None:
            continue
        for volume in pod.spec.volumes:
            if volume.persistentVolumeClaim is None:
                # not a pv
                continue
            pvc_name = volume.persistentVolumeClaim.claimName
            pvc.append(
                client.get(
                    res=core_v1.PersistentVolumeClaim,
                    name=pvc_name,
                    namespace=pod.metadata.namespace,  # type: ignore
                )
            )
    return pvc


def delete_pvc(
    client: "l_client.Client", pvcs: list["core_v1.PersistentVolumeClaim"]
) -> None:
    for pvc in pvcs:
        if pvc.metadata is None:
            continue
        LOG.debug("Deleting PVC %s", pvc.metadata.name)
        client.delete(
            core_v1.PersistentVolumeClaim,
            pvc.metadata.name,  # type: ignore
            namespace=pvc.metadata.namespace,  # type: ignore
            grace_period=0,
            cascade=l_types.CascadeType.FOREGROUND,
        )


def drain(client: "l_client.Client", name: str):
    """Evict all pods from a node."""
    pods = fetch_pods_for_eviction(client, name)
    pvcs = fetch_pvc(client, pods)
    evict_pods(client, pods)
    delete_pvc(client, pvcs)
