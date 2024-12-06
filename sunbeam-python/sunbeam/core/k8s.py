# Copyright (c) 2024 Canonical Ltd.
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
import logging
from typing import Type

from lightkube import ApiError
from lightkube.core.client import Client as KubeClient
from lightkube.generic_resource import (
    GenericNamespacedResource,
    create_namespaced_resource,
)
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Node, PersistentVolumeClaim, Pod
from lightkube.types import CascadeType
from snaphelpers import Snap  # noqa: F401 - required for test mocks

from sunbeam.core.common import SunbeamException

LOG = logging.getLogger(__name__)

# --- K8s specific
K8S_DEFAULT_STORAGECLASS = "csi-rawfile-default"
K8S_KUBECONFIG_KEY = "K8SKubeConfig"
SERVICE_LB_ANNOTATION = "io.cilium/lb-ipam-ips"

# --- Metallb specific
METALLB_IP_ANNOTATION = "metallb.universe.tf/loadBalancerIPs"
METALLB_ADDRESS_POOL_ANNOTATION = "metallb.universe.tf/address-pool"
METALLB_ALLOCATED_POOL_ANNOTATION = "metallb.universe.tf/ip-allocated-from-pool"

CREDENTIAL_SUFFIX = "-creds"
K8S_CLOUD_SUFFIX = "-k8s"
LOADBALANCER_QUESTION_DESCRIPTION = """\
OpenStack services are exposed via virtual IP addresses.\
 This range should contain at least ten addresses\
 and must not overlap with external network CIDR.\
 To access APIs from a remote host, the range must reside\
 within the subnet that the primary network interface is on.\
"""


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
    def get_lightkube_loadbalancer_resource(cls) -> Type[GenericNamespacedResource]:
        """Return lighkube generic resource of type loadbalancer."""
        return create_namespaced_resource(
            "metallb.io",
            "v1beta1",
            "IPAddressPool",
            "ipaddresspools",
            verbs=["delete", "get", "list", "patch", "post", "put"],
        )

    @classmethod
    def get_lightkube_l2_advertisement_resource(cls) -> Type[GenericNamespacedResource]:
        """Return lighkube generic resource of type l2advertisement."""
        return create_namespaced_resource(
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


def find_node(client: KubeClient, name: str) -> Node:
    """Find a node by name."""
    try:
        return client.get(Node, name)
    except ApiError as e:
        if e.status.code == 404:
            raise K8SNodeNotFoundError(f"Node {name} not found")
        raise K8SError(f"Failed to get node {name}") from e


def cordon(client: KubeClient, name: str):
    """Taint a node as unschedulable."""
    LOG.debug("Marking %s unschedulable", name)
    try:
        client.patch(Node, name, {"spec": {"unschedulable": True}})
    except ApiError as e:
        if e.status.code == 404:
            raise K8SNodeNotFoundError(f"Node {name} not found")
        raise K8SError(f"Failed to patch node {name}") from e


def is_not_daemonset(pod):
    return pod.metadata.ownerReferences[0].kind != "DaemonSet"


def fetch_pods(
    client: KubeClient,
    namespace: str | None = None,
    labels: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
) -> list[Pod]:
    """Fetch all pods on node that can be evicted.

    DaemonSet pods cannot be evicted as they don't respect unschedulable flag.
    """
    return list(
        client.list(
            res=Pod,
            namespace=namespace if namespace else "*",
            labels=labels,  # type: ignore
            fields=fields,  # type: ignore
        )
    )


def fetch_pods_for_eviction(
    client: KubeClient, node_name: str, labels: dict[str, str] | None = None
) -> list[Pod]:
    """Fetch all pods on node that can be evicted.

    DaemonSet pods cannot be evicted as they don't respect unschedulable flag.
    """
    pods = fetch_pods(
        client,
        labels=labels,
        fields={"spec.nodeName": node_name},
    )
    return list(filter(is_not_daemonset, pods))


def evict_pods(client: KubeClient, pods: list[Pod]) -> None:
    for pod in pods:
        if pod.metadata is None:
            continue
        LOG.debug(f"Evicting pod {pod.metadata.name}")
        evict = Pod.Eviction(
            metadata=ObjectMeta(
                name=pod.metadata.name, namespace=pod.metadata.namespace
            ),
        )
        client.create(evict, name=str(pod.metadata.name))


def fetch_pvc(client: KubeClient, pods: list[Pod]) -> list[PersistentVolumeClaim]:
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
                    res=PersistentVolumeClaim,
                    name=pvc_name,
                    namespace=pod.metadata.namespace,  # type: ignore
                )
            )
    return pvc


def delete_pvc(client: KubeClient, pvcs: list[PersistentVolumeClaim]) -> None:
    for pvc in pvcs:
        if pvc.metadata is None:
            continue
        LOG.debug("Deleting PVC %s", pvc.metadata.name)
        client.delete(
            PersistentVolumeClaim,
            pvc.metadata.name,  # type: ignore
            namespace=pvc.metadata.namespace,  # type: ignore
            grace_period=0,
            cascade=CascadeType.FOREGROUND,
        )


def drain(client: KubeClient, name: str):
    """Evict all pods from a node."""
    pods = fetch_pods_for_eviction(client, name)
    pvcs = fetch_pvc(client, pods)
    evict_pods(client, pods)
    delete_pvc(client, pvcs)
