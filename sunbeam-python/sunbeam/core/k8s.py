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

from snaphelpers import Snap

from sunbeam.core.common import SunbeamException

LOG = logging.getLogger(__name__)

# --- Microk8s specific
MICROK8S_KUBECONFIG_KEY = "Microk8sConfig"
MICROK8S_DEFAULT_STORAGECLASS = "microk8s-hostpath"
METALLB_ANNOTATION = "metallb.universe.tf/loadBalancerIPs"

# --- K8s specific
K8S_DEFAULT_STORAGECLASS = "csi-rawfile-default"
K8S_KUBECONFIG_KEY = "K8SKubeConfig"
SERVICE_LB_ANNOTATION = "io.cilium/lb-ipam-ips"

SUPPORTED_K8S_PROVIDERS = ["k8s", "microk8s"]
CREDENTIAL_SUFFIX = "-creds"
K8S_CLOUD_SUFFIX = "-k8s"


class K8SHelper:
    """K8S Helper that provides cloud constants."""

    @classmethod
    def get_provider(cls) -> str:
        """Return k8s provider from snap settings."""
        provider = Snap().config.get("k8s.provider")
        if provider not in SUPPORTED_K8S_PROVIDERS:
            raise SunbeamException(
                f"k8s provider should be one of {SUPPORTED_K8S_PROVIDERS}"
            )

        return provider

    @classmethod
    def get_cloud(cls, deployment_name: str) -> str:
        """Return cloud name matching provider."""
        return f"{deployment_name}{K8S_CLOUD_SUFFIX}"

    @classmethod
    def get_default_storageclass(cls) -> str:
        """Return storageclass matching provider."""
        match cls.get_provider():
            case "k8s":
                return K8S_DEFAULT_STORAGECLASS
            case _:
                return MICROK8S_DEFAULT_STORAGECLASS

    @classmethod
    def get_kubeconfig_key(cls) -> str:
        """Return kubeconfig key matching provider."""
        match cls.get_provider():
            case "k8s":
                return K8S_KUBECONFIG_KEY
            case _:
                return MICROK8S_KUBECONFIG_KEY

    @classmethod
    def get_loadbalancer_annotation(cls) -> str:
        """Return loadbalancer annotation matching provider."""
        match cls.get_provider():
            case "k8s":
                return SERVICE_LB_ANNOTATION
            case _:
                return METALLB_ANNOTATION
