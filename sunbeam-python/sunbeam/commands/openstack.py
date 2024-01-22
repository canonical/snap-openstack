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

import logging
from typing import Optional

from lightkube.core import exceptions
from lightkube.core.client import Client as KubeClient
from lightkube.core.client import KubeConfig
from lightkube.resources.core_v1 import Service
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.microceph import APPLICATION as MICROCEPH_APPLICATION
from sunbeam.commands.microk8s import (
    CREDENTIAL_SUFFIX,
    MICROK8S_CLOUD,
    MICROK8S_DEFAULT_STORAGECLASS,
    MICROK8S_KUBECONFIG_KEY,
)
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs.common import (
    RAM_32_GB_IN_KB,
    BaseStep,
    Result,
    ResultType,
    get_host_total_ram,
    read_config,
    update_config,
    update_status_background,
)
from sunbeam.jobs.juju import (
    CONTROLLER_MODEL,
    JujuHelper,
    JujuWaitException,
    ModelNotFoundException,
    TimeoutException,
    run_sync,
)
from sunbeam.versions import OPENSTACK_CHANNEL, OVN_CHANNEL, RABBITMQ_CHANNEL

LOG = logging.getLogger(__name__)
OPENSTACK_MODEL = "openstack"
OPENSTACK_DEPLOY_TIMEOUT = 5400  # 90 minutes
METALLB_ANNOTATION = "metallb.universe.tf/loadBalancerIPs"

CONFIG_KEY = "TerraformVarsOpenstack"
TOPOLOGY_KEY = "Topology"


def determine_target_topology_at_bootstrap() -> str:
    """Determines the target topology at bootstrap time.

    Under a threshold of 20GiB RAM on the bootstrapping node,
    target is considered to be 'single'
    Otherwise, target is considered to be 'multi'
    """
    host_total_ram = get_host_total_ram()
    if host_total_ram < RAM_32_GB_IN_KB:
        return "single"
    return "multi"


def determine_target_topology(client: Client) -> str:
    """Determines the target topology.

    Use information from clusterdb to infer deployment
    topology.
    """
    control_nodes = client.cluster.list_nodes_by_role("control")
    compute_nodes = client.cluster.list_nodes_by_role("compute")
    combined = set(node["name"] for node in control_nodes + compute_nodes)
    host_total_ram = get_host_total_ram()
    if len(combined) == 1 and host_total_ram < RAM_32_GB_IN_KB:
        topology = "single"
    elif len(combined) < 10:
        topology = "multi"
    else:
        topology = "large"
    LOG.debug(f"Auto-detected topology: {topology}")
    return topology


def compute_ha_scale(topology: str, control_nodes: int) -> int:
    if topology == "single" or control_nodes < 3:
        return 1
    return 3


def compute_os_api_scale(topology: str, control_nodes: int) -> int:
    if topology == "single":
        return 1
    if topology == "multi":
        return min(control_nodes, 3)
    if topology == "large":
        return min(control_nodes + 2, 7)
    raise ValueError(f"Unknown topology {topology}")


def compute_ingress_scale(topology: str, control_nodes: int) -> int:
    if topology == "single":
        return 1
    return control_nodes


def compute_ceph_replica_scale(topology: str, storage_nodes: int) -> int:
    if topology == "single" or storage_nodes < 2:
        return 1
    return min(storage_nodes, 3)


class DeployControlPlaneStep(BaseStep, JujuStepHelper):
    """Deploy OpenStack using Terraform cloud"""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        topology: str,
        database: str,
    ):
        super().__init__(
            "Deploying OpenStack Control Plane",
            "Deploying OpenStack Control Plane to Kubernetes (this may take a while)",
        )
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.topology = topology
        self.database = database
        self.model = OPENSTACK_MODEL
        self.cloud = MICROK8S_CLOUD
        self.client = client

    def get_storage_tfvars(self) -> dict:
        """Create terraform variables related to storage."""
        tfvars = {}
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        if storage_nodes:
            tfvars["enable-ceph"] = True
            tfvars["ceph-offer-url"] = f"{CONTROLLER_MODEL}.{MICROCEPH_APPLICATION}"
        else:
            tfvars["enable-ceph"] = False

        return tfvars

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        self.update_status(status, "determining appropriate configuration")
        try:
            previous_config = read_config(self.client, TOPOLOGY_KEY)
        except ConfigItemNotFoundException:
            # Config was never registered in database
            previous_config = {}

        determined_topology = determine_target_topology_at_bootstrap()

        if self.topology == "auto":
            self.topology = previous_config.get("topology", determined_topology)
        LOG.debug(f"Bootstrap: topology {self.topology}")

        if self.database == "auto":
            self.database = previous_config.get("database", determined_topology)
        LOG.debug(f"Bootstrap: database topology {self.database}")

        if (database := previous_config.get("database")) and database != self.database:
            return Result(
                ResultType.FAILED,
                "Database topology cannot be changed, please destroy and re-bootstrap",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        # TODO(jamespage):
        # This needs to evolve to add support for things like:
        # - Enabling HA
        # - Enabling/disabling specific services
        # - Switch channels for the charmed operators
        update_config(
            self.client,
            TOPOLOGY_KEY,
            {"topology": self.topology, "database": self.database},
        )

        # Always get terraform variables from cluster database and
        # update them. This is to ensure not to skip the terraform
        # variables updated by plugins.
        try:
            tfvars = read_config(self.client, self._CONFIG)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars.update(
            {
                "model": self.model,
                # Make these channel options configurable by the user
                "openstack-channel": OPENSTACK_CHANNEL,
                "ovn-channel": OVN_CHANNEL,
                "rabbitmq-channel": RABBITMQ_CHANNEL,
                "cloud": self.cloud,
                "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
                "config": {"workload-storage": MICROK8S_DEFAULT_STORAGECLASS},
                "many-mysql": self.database == "multi",
            }
        )
        tfvars.update(self.get_storage_tfvars())
        update_config(self.client, self._CONFIG, tfvars)
        self.tfhelper.write_tfvars(tfvars)
        self.update_status(status, "deploying services")
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error configuring cloud")
            return Result(ResultType.FAILED, str(e))

        # Remove cinder-ceph from apps to wait on if ceph is not enabled
        apps = run_sync(self.jhelper.get_application_names(self.model))
        if not tfvars.get("enable-ceph") and "cinder-ceph" in apps:
            apps.remove("cinder-ceph")
        LOG.debug(f"Application monitored for readiness: {apps}")
        task = run_sync(update_status_background(self, apps, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class ResizeControlPlaneStep(BaseStep, JujuStepHelper):
    """Resize OpenStack using Terraform cloud."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        topology: str,
        force: bool,
    ):
        super().__init__(
            "Resizing OpenStack Control Plane",
            "Resizing OpenStack Control Plane to match appropriate topology",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.topology = topology
        self.force = force
        self.model = OPENSTACK_MODEL

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            run_sync(self.jhelper.get_model(OPENSTACK_MODEL))
        except ModelNotFoundException:
            return Result(
                ResultType.FAILED,
                "OpenStack control plane is not deployed, cannot resize",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Execute configuration using terraform."""
        self.update_status(status, "fetching configuration")
        topology_dict = read_config(self.client, TOPOLOGY_KEY)
        if self.topology == "auto":
            topology = determine_target_topology(self.client)
        else:
            topology = self.topology
        topology_dict["topology"] = topology
        is_not_compatible = (
            topology_dict["database"] == "single" and topology == "large"
        )
        if not self.force and is_not_compatible:
            return Result(
                ResultType.FAILED,
                (
                    "Cannot resize control plane to large with single database,"
                    " use -f/--force to override"
                ),
            )
        update_config(
            self.client,
            TOPOLOGY_KEY,
            topology_dict,
        )
        tf_vars = read_config(self.client, self._CONFIG)
        control_nodes = self.client.cluster.list_nodes_by_role("control")
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        # NOTE(jamespage)
        # When dedicated control nodes are used, ceph is not enabled during
        # bootstrap - however storage nodes may be added later so re-assess
        tf_vars.update(
            {
                "ha-scale": compute_ha_scale(topology, len(control_nodes)),
                "os-api-scale": compute_os_api_scale(topology, len(control_nodes)),
                "ingress-scale": compute_ingress_scale(topology, len(control_nodes)),
                "ceph-osd-replication-count": compute_ceph_replica_scale(
                    topology, len(storage_nodes)
                ),
                "enable-ceph": len(storage_nodes) > 0,
                "ceph-offer-url": f"{CONTROLLER_MODEL}.{MICROCEPH_APPLICATION}",
            }
        )

        update_config(self.client, self._CONFIG, tf_vars)
        self.update_status(status, "scaling services")
        self.tfhelper.write_tfvars(tf_vars)
        try:
            self.tfhelper.apply()
        except TerraformException as e:
            LOG.exception("Error resizing control plane")
            return Result(ResultType.FAILED, str(e))

        # Remove cinder-ceph from apps to wait on if ceph is not enabled
        apps = run_sync(self.jhelper.get_application_names(self.model))
        if not storage_nodes and "cinder-ceph" in apps:
            apps.remove("cinder-ceph")
        task = run_sync(update_status_background(self, apps, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class PatchLoadBalancerServicesStep(BaseStep):
    SERVICES = ["traefik", "traefik-public", "rabbitmq", "ovn-relay"]
    MODEL = OPENSTACK_MODEL

    def __init__(
        self,
        client: Client,
    ):
        super().__init__(
            "Patch LoadBalancer services",
            "Patch LoadBalancer service annotations",
        )
        self.client = client

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Determines if the step should be skipped or not.

        :return: ResultType.SKIPPED if the Step should be skipped,
                ResultType.COMPLETED or ResultType.FAILED otherwise
        """
        try:
            self.kubeconfig = read_config(self.client, MICROK8S_KUBECONFIG_KEY)
        except ConfigItemNotFoundException:
            LOG.debug("MicroK8S config not found", exc_info=True)
            return Result(ResultType.FAILED, "MicroK8S config not found")

        kubeconfig = KubeConfig.from_dict(self.kubeconfig)
        try:
            self.kube = KubeClient(kubeconfig, self.MODEL, trust_env=False)
        except exceptions.ConfigError as e:
            LOG.debug("Error creating k8s client", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        for service_name in self.SERVICES:
            service = self.kube.get(Service, service_name)
            service_annotations = service.metadata.annotations
            if METALLB_ANNOTATION not in service_annotations:
                return Result(ResultType.COMPLETED)

        return Result(ResultType.SKIPPED)

    def run(self, status: Optional[Status] = None) -> Result:
        """Patch LoadBalancer services annotations with MetalLB IP."""
        for service_name in self.SERVICES:
            service = self.kube.get(Service, service_name)
            service_annotations = service.metadata.annotations
            if METALLB_ANNOTATION not in service_annotations:
                loadbalancer_ip = service.status.loadBalancer.ingress[0].ip
                service_annotations[METALLB_ANNOTATION] = loadbalancer_ip
                LOG.debug(f"Patching {service_name!r} to use IP {loadbalancer_ip!r}")
                self.kube.patch(Service, service_name, obj=service)

        return Result(ResultType.COMPLETED)
