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

import asyncio
import logging
import math

from rich.console import Console
from rich.status import Status

import sunbeam.commands.microceph as microceph
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.commands.juju import JujuStepHelper
from sunbeam.commands.terraform import TerraformException, TerraformHelper
from sunbeam.jobs.common import (
    RAM_32_GB_IN_KB,
    BaseStep,
    Result,
    ResultType,
    convert_proxy_to_model_configs,
    get_host_total_ram,
    read_config,
    update_config,
    update_status_background,
)
from sunbeam.jobs.deployment import Deployment
from sunbeam.jobs.juju import JujuHelper, JujuWaitException, TimeoutException, run_sync
from sunbeam.jobs.k8s import CREDENTIAL_SUFFIX, K8SHelper
from sunbeam.jobs.manifest import Manifest
from sunbeam.jobs.questions import (
    PromptQuestion,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.jobs.steps import PatchLoadBalancerServicesStep

LOG = logging.getLogger(__name__)
OPENSTACK_MODEL = "openstack"
OPENSTACK_DEPLOY_TIMEOUT = 5400  # 90 minutes

CONFIG_KEY = "TerraformVarsOpenstack"
TOPOLOGY_KEY = "Topology"
DATABASE_MEMORY_KEY = "DatabaseMemory"
REGION_CONFIG_KEY = "Region"
DEFAULT_REGION = "RegionOne"

DATABASE_MAX_POOL_SIZE = 2
DATABASE_ADDITIONAL_BUFFER_SIZE = 600
DATABASE_OVERSIZE_FACTOR = 1.2
MB_BYTES_PER_CONNECTION = 12


# This dict maps every databases that can be used by OpenStack services
# to the number of processes each service needs to the database.
CONNECTIONS = {
    "cinder": {
        "cinder-ceph-k8s": 4,
        "cinder-k8s": 5,
    },
    "glance": {"glance-k8s": 5},
    # Horizon does not pool connections, actual value is 40
    # but set it to 20 because of max_pool_size multiplier
    "horizon": {"horizon-k8s": 20},
    "keystone": {"keystone-k8s": 5},
    "neutron": {"neutron-k8s": 8 * 2},
    "nova": {"nova-k8s": 8 * 3},
    "placement": {"placement-k8s": 4},
}


def determine_target_topology(client: Client) -> str:
    """Determines the target topology.

    Use information from clusterdb to infer deployment
    topology.
    """
    control_nodes = client.cluster.list_nodes_by_role("control")
    compute_nodes = client.cluster.list_nodes_by_role("compute")
    combined = {node["name"] for node in control_nodes + compute_nodes}
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
    if topology == "multi" or control_nodes < 3:
        return min(control_nodes, 3)
    if topology == "large":
        return min(control_nodes + 2, 7)
    raise ValueError(f"Unknown topology {topology}")


def compute_ingress_scale(topology: str, control_nodes: int) -> int:
    if topology == "single":
        return 1
    return min(control_nodes, 3)


def compute_resources_for_service(
    database: dict[str, int], max_pool_size: int
) -> tuple[int, int]:
    """Compute resources needed for a single unit service."""
    memory_needed = 0
    total_connections = 0
    for process in database.values():
        # each service needs, in the worst case, max_pool_size connections
        # per process, plus one for its mysql router
        nb_connections = max_pool_size * process + 1
        total_connections += nb_connections
        memory_needed += nb_connections * MB_BYTES_PER_CONNECTION
    return total_connections, memory_needed


def get_database_resource_dict(client: Client) -> dict[str, tuple[int, int]]:
    """Returns a dict containing the resource allocation for each database service.

    Resource allocation is only for a single unit service.
    """
    try:
        resource_dict = read_config(client, DATABASE_MEMORY_KEY)
    except ConfigItemNotFoundException:
        resource_dict = {}
    resource_dict.update(
        {
            service: compute_resources_for_service(connection, DATABASE_MAX_POOL_SIZE)
            for service, connection in CONNECTIONS.items()
        }
    )

    return resource_dict


def write_database_resource_dict(
    client: Client, resource_dict: dict[str, tuple[int, int]]
):
    """Write the resource allocation for each database service."""
    update_config(client, DATABASE_MEMORY_KEY, resource_dict)


def get_database_tfvars(
    many_mysql: bool, resource_dict: dict[str, tuple[int, int]], service_scale: int
) -> dict[str, bool | dict]:
    """Create terraform variables related to database."""
    tfvars: dict[str, bool | dict] = {"many-mysql": many_mysql}

    if many_mysql:
        tfvars["mysql-config-map"] = {
            service: {
                "profile-limit-memory": (
                    math.ceil(memory * service_scale * DATABASE_OVERSIZE_FACTOR)
                )
                + DATABASE_ADDITIONAL_BUFFER_SIZE,
                "experimental-max-connections": math.floor(
                    connections * service_scale * DATABASE_OVERSIZE_FACTOR
                ),
            }
            for service, (connections, memory) in resource_dict.items()
        }
    else:
        connections, memories = list(zip(*resource_dict.values()))
        total_memory = (
            math.ceil(sum(memories) * service_scale * DATABASE_OVERSIZE_FACTOR)
            + DATABASE_ADDITIONAL_BUFFER_SIZE
        )
        tfvars["mysql-config"] = {
            "profile-limit-memory": (total_memory),
            "experimental-max-connections": math.floor(
                sum(connections) * service_scale * DATABASE_OVERSIZE_FACTOR
            ),
        }

    return tfvars


class DeployControlPlaneStep(BaseStep, JujuStepHelper):
    """Deploy OpenStack using Terraform cloud."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        deployment: Deployment,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        topology: str,
        database: str,
        machine_model: str,
        proxy_settings: dict | None = None,
        force: bool = False,
    ):
        super().__init__(
            "Deploying OpenStack Control Plane",
            "Deploying OpenStack Control Plane to Kubernetes (this may take a while)",
        )
        self.client = deployment.get_client()
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.topology = topology
        self.database = database
        self.machine_model = machine_model
        self.proxy_settings = proxy_settings or {}
        self.force = force
        self.model = OPENSTACK_MODEL
        self.cloud = K8SHelper.get_cloud(deployment.name)

    def get_storage_tfvars(self, storage_nodes: list[dict]) -> dict:
        """Create terraform variables related to storage."""
        tfvars: dict[str, str | bool | int] = {}
        if storage_nodes:
            model_with_owner = self.get_model_name_with_owner(self.machine_model)
            tfvars["enable-ceph"] = True
            tfvars["ceph-offer-url"] = f"{model_with_owner}.{microceph.APPLICATION}"
            tfvars["ceph-osd-replication-count"] = microceph.ceph_replica_scale(
                len(storage_nodes)
            )
        else:
            tfvars["enable-ceph"] = False

        return tfvars

    def get_database_tfvars(self, service_scale: int) -> dict:
        """Create terraform variables related to database."""
        many_mysql = self.database == "multi"
        resource_dict = get_database_resource_dict(self.client)
        write_database_resource_dict(self.client, resource_dict)
        return get_database_tfvars(many_mysql, resource_dict, service_scale)

    def get_region_tfvars(self) -> dict:
        """Create terraform variables related to region."""
        return {"region": read_config(self.client, REGION_CONFIG_KEY)["region"]}

    def is_skip(self, status: Status | None = None) -> Result:
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

        determined_topology = determine_target_topology(self.client)

        if self.topology == "auto":
            self.topology = determined_topology
        LOG.debug(f"topology {self.topology}")

        if self.database == "auto":
            self.database = previous_config.get("database", determined_topology)
        if self.database == "large":
            # multi and large are the same
            self.database = "multi"
        LOG.debug(f"database topology {self.database}")
        if (database := previous_config.get("database")) and database != self.database:
            return Result(
                ResultType.FAILED,
                "Database topology cannot be changed, please destroy and re-bootstrap",
            )

        is_not_compatible = self.database == "single" and self.topology == "large"
        if not self.force and is_not_compatible:
            return Result(
                ResultType.FAILED,
                (
                    "Cannot deploy control plane to large with single database,"
                    " use -f/--force to override"
                ),
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Execute configuration using terraform."""
        # TODO(jamespage):
        # This needs to evolve to add support for things like:
        # - Enabling/disabling specific services
        update_config(
            self.client,
            TOPOLOGY_KEY,
            {"topology": self.topology, "database": self.database},
        )

        self.update_status(status, "fetching cluster nodes")
        control_nodes = self.client.cluster.list_nodes_by_role("control")
        storage_nodes = self.client.cluster.list_nodes_by_role("storage")

        self.update_status(status, "computing deployment sizing")
        model_config = convert_proxy_to_model_configs(self.proxy_settings)
        model_config.update({"workload-storage": K8SHelper.get_default_storageclass()})
        service_scale = compute_os_api_scale(self.topology, len(control_nodes))
        extra_tfvars = self.get_storage_tfvars(storage_nodes)
        extra_tfvars.update(self.get_region_tfvars())
        extra_tfvars.update(self.get_database_tfvars(service_scale))
        extra_tfvars.update(
            {
                "model": self.model,
                "cloud": self.cloud,
                "credential": f"{self.cloud}{CREDENTIAL_SUFFIX}",
                "config": model_config,
                "ha-scale": compute_ha_scale(self.topology, len(control_nodes)),
                "os-api-scale": service_scale,
                "ingress-scale": compute_ingress_scale(
                    self.topology, len(control_nodes)
                ),
            }
        )
        self.update_status(status, "deploying services")
        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=extra_tfvars,
            )
        except TerraformException as e:
            LOG.exception("Error configuring cloud")
            return Result(ResultType.FAILED, str(e))

        # Remove cinder-ceph from apps to wait on if ceph is not enabled
        apps = run_sync(self.jhelper.get_application_names(self.model))
        if not extra_tfvars.get("enable-ceph") and "cinder-ceph" in apps:
            apps.remove("cinder-ceph")
        LOG.debug(f"Application monitored for readiness: {apps}")
        queue: asyncio.queues.Queue[str] = asyncio.queues.Queue(maxsize=len(apps))
        task = run_sync(update_status_background(self, apps, queue, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                    queue=queue,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class OpenStackPatchLoadBalancerServicesStep(PatchLoadBalancerServicesStep):
    def __init__(
        self,
        client: Client,
    ):
        super().__init__(client)

    def services(self):
        """List of services to patch."""
        services = ["traefik", "traefik-public", "rabbitmq", "ovn-relay"]
        if self.client.cluster.list_nodes_by_role("storage"):
            services.append("traefik-rgw")
        return services

    def model(self):
        """Name of the model to use."""
        return OPENSTACK_MODEL


class ReapplyOpenStackTerraformPlanStep(BaseStep, JujuStepHelper):
    """Reapply OpenStack Terraform plan."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
    ):
        super().__init__(
            "Applying Control plane Terraform plan",
            "Applying Control plane Terraform plan (this may take a while)",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.model = OPENSTACK_MODEL

    def run(self, status: Status | None = None) -> Result:
        """Reapply Terraform plan if there are changes in tfvars."""
        try:
            self.update_status(status, "deploying services")
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
            )
        except TerraformException as e:
            LOG.exception("Error reconfiguring cloud")
            return Result(ResultType.FAILED, str(e))

        storage_nodes = self.client.cluster.list_nodes_by_role("storage")
        # Remove cinder-ceph from apps to wait on if ceph is not enabled
        apps = run_sync(self.jhelper.get_application_names(self.model))
        if not storage_nodes and "cinder-ceph" in apps:
            apps.remove("cinder-ceph")
        LOG.debug(f"Application monitored for readiness: {apps}")
        queue: asyncio.queues.Queue[str] = asyncio.queues.Queue(maxsize=len(apps))
        task = run_sync(update_status_background(self, apps, queue, status))
        try:
            run_sync(
                self.jhelper.wait_until_active(
                    self.model,
                    apps,
                    timeout=OPENSTACK_DEPLOY_TIMEOUT,
                    queue=queue,
                )
            )
        except (JujuWaitException, TimeoutException) as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, str(e))
        finally:
            if not task.done():
                task.cancel()

        return Result(ResultType.COMPLETED)


class UpdateOpenStackModelConfigStep(BaseStep):
    """Update OpenStack ModelConfig via Terraform plan."""

    _CONFIG = CONFIG_KEY

    def __init__(
        self,
        client: Client,
        tfhelper: TerraformHelper,
        manifest: Manifest,
        model_config: dict,
    ):
        super().__init__(
            "Update OpenStack model config",
            "Updating OpenStack model config related to proxy",
        )
        self.client = client
        self.tfhelper = tfhelper
        self.manifest = manifest
        self.model_config = model_config

    def run(self, status: Status | None = None) -> Result:
        """Apply model configs to openstack terraform plan."""
        try:
            self.model_config.update(
                {"workload-storage": K8SHelper.get_default_storageclass()}
            )
            override_tfvars = {"config": self.model_config}
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self._CONFIG,
                override_tfvars=override_tfvars,
                tf_apply_extra_args=["-target=juju_model.sunbeam"],
            )
            return Result(ResultType.COMPLETED)
        except TerraformException as e:
            LOG.exception("Error updating modelconfigs for openstack plan")
            return Result(ResultType.FAILED, str(e))


def region_questions():
    return {
        "region": PromptQuestion(
            "Enter a region name (cannot be changed later)",
            default_value=DEFAULT_REGION,
        )
    }


class PromptRegionStep(BaseStep):
    """Prompt user for region."""

    def __init__(
        self,
        client: Client,
        deployment_preseed: dict | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("Region", "Query user for region")
        self.client = client
        self.preseed = deployment_preseed or {}
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = load_answers(self.client, REGION_CONFIG_KEY)

        if region := self.variables.get("region"):
            # Region cannot be modified once set
            LOG.debug(f"Region already set to {region}")
            return
        region_bank = QuestionBank(
            questions=region_questions(),
            console=console,
            preseed=self.preseed,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
        )
        self.variables["region"] = region_bank.region.ask()
        write_answers(self.client, REGION_CONFIG_KEY, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return True

    def run(self, status: Status | None) -> Result:
        """Run the step to completion.

        Invoked when the step is run and returns a ResultType to indicate
        :return:
        """
        return Result(ResultType.COMPLETED, f"Region set to {self.variables['region']}")
