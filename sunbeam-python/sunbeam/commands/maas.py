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

"""MAAS management."""

import builtins
import collections
import enum
import logging
import ssl
import textwrap
from pathlib import Path
from typing import TypeGuard, overload

import pydantic
import yaml
from juju.controller import Controller
from maas.client import bones, connect
from rich.console import Console
from rich.status import Status

from sunbeam.commands.deployment import Deployment, DeploymentsConfig
from sunbeam.commands.juju import (
    BootstrapJujuStep,
    ControllerNotFoundException,
    JujuStepHelper,
    ScaleJujuStep,
)
from sunbeam.jobs.checks import Check, DiagnosticsCheck, DiagnosticsResult
from sunbeam.jobs.common import (
    RAM_4_GB_IN_MB,
    RAM_32_GB_IN_MB,
    BaseStep,
    Result,
    ResultType,
)
from sunbeam.jobs.juju import JujuAccount, JujuController

LOG = logging.getLogger(__name__)
console = Console()

MAAS_CONFIG = "maas.yaml"


class Networks(enum.Enum):
    PUBLIC = "public"
    STORAGE = "storage"
    STORAGE_CLUSTER = "storage-cluster"
    INTERNAL = "internal"
    DATA = "data"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasDeployment(Deployment):
    type: str = "maas"
    token: str
    resource_pool: str
    network_mapping: dict[str, str | None] = {}
    juju_account: JujuAccount | None = None
    juju_controller: JujuController | None = None

    @property
    def controller(self) -> str:
        """Return controller name."""
        return self.name + "-controller"

    @pydantic.validator("type")
    def type_validator(cls, v: str, values: dict) -> str:
        if v != "maas":
            raise ValueError("Deployment type must be MAAS.")
        return v

    def get_connected_controller(self) -> Controller:
        """Return connected controller."""
        if self.juju_account is None:
            raise ValueError(f"No juju account configured for deployment {self.name}.")
        if self.juju_controller is None:
            raise ValueError(
                f"No juju controller configured for deployment {self.name}."
            )
        return self.juju_controller.to_controller(self.juju_account)


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return isinstance(deployment, MaasDeployment)


class RoleTags(enum.Enum):
    CONTROL = "control"
    COMPUTE = "compute"
    STORAGE = "storage"
    JUJU_CONTROLLER = "juju-controller"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


ROLE_NETWORK_MAPPING = {
    RoleTags.CONTROL: [
        Networks.INTERNAL,
        Networks.PUBLIC,
        Networks.STORAGE,
    ],
    RoleTags.COMPUTE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.STORAGE,
    ],
    RoleTags.STORAGE: [
        Networks.DATA,
        Networks.INTERNAL,
        Networks.STORAGE,
        Networks.STORAGE_CLUSTER,
    ],
    RoleTags.JUJU_CONTROLLER: [
        Networks.INTERNAL,
        # TODO(gboutry): missing public access network to reach charmhub...
    ],
}


class StorageTags(enum.Enum):
    CEPH = "ceph"

    @classmethod
    def values(cls) -> list[str]:
        """Return list of tag values."""
        return [tag.value for tag in cls]


class MaasClient:
    """Facade to MAAS APIs."""

    def __init__(self, url: str, token: str, resource_pool: str | None = None):
        self._client = connect(url, apikey=token)
        self.resource_pool = resource_pool

    def get_resource_pool(self, name: str) -> object:
        """Fetch resource pool from MAAS."""
        return self._client.resource_pools.get(name)  # type: ignore

    def list_machines(self, **kwargs) -> list[dict]:
        """List machines."""
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        try:
            return self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        except bones.CallError as e:
            if "No such pool" in str(e):
                raise ValueError(f"Resource pool {self.resource_pool!r} not found.")
            raise e

    def get_machine(self, machine: str) -> dict:
        """Get machine."""
        kwargs = {
            "hostname": machine,
        }
        if self.resource_pool:
            kwargs["pool"] = self.resource_pool
        machines = self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        if len(machines) == 0:
            raise ValueError(f"Machine {machine!r} not found.")
        if len(machines) > 1:
            raise ValueError(f"Machine {machine!r} not unique.")
        return machines[0]

    def list_spaces(self) -> list[dict]:
        """List spaces."""
        return self._client.spaces.list.__self__._handler.read()  # type: ignore

    @classmethod
    def from_deployment(cls, deployment: Deployment) -> "MaasClient":
        """Return client connected to active deployment."""
        if not is_maas_deployment(deployment):
            raise ValueError("Deployment is not a MAAS deployment.")
        return cls(
            deployment.url,
            deployment.token,
            deployment.resource_pool,
        )


def _convert_raw_machine(machine_raw: dict) -> dict:
    storage_tags = collections.Counter()
    for blockdevice in machine_raw["blockdevice_set"]:
        storage_tags.update(set(blockdevice["tags"]).intersection(StorageTags.values()))

    spaces = []
    for interface in machine_raw["interface_set"]:
        spaces.append(interface["vlan"]["space"])
    return {
        "hostname": machine_raw["hostname"],
        "roles": list(set(machine_raw["tag_names"]).intersection(RoleTags.values())),
        "zone": machine_raw["zone"]["name"],
        "status": machine_raw["status_name"],
        "storage": dict(storage_tags),
        "spaces": list(set(spaces)),
        "cores": machine_raw["cpu_count"],
        "memory": machine_raw["memory"],
    }


def list_machines(client: MaasClient, **extra_args) -> list[dict]:
    """List machines in deployment, return consumable list of dicts."""
    machines_raw = client.list_machines(**extra_args)

    machines = []
    for machine in machines_raw:
        machines.append(_convert_raw_machine(machine))
    return machines


def get_machine(client: MaasClient, machine: str) -> dict:
    """Get machine in deployment, return consumable dict."""
    machine_raw = client.get_machine(machine)
    return _convert_raw_machine(machine_raw)


def _group_machines_by_zone(machines: list[dict]) -> dict[str, list[dict]]:
    """Helper to list machines by zone, return consumable dict."""
    result = collections.defaultdict(list)
    for machine in machines:
        result[machine["zone"]].append(machine)
    return dict(result)


def list_machines_by_zone(client: MaasClient) -> dict[str, list[dict]]:
    """List machines by zone, return consumable dict."""
    machines_raw = list_machines(client)
    return _group_machines_by_zone(machines_raw)


def list_spaces(client: MaasClient) -> list[dict]:
    """List spaces in deployment, return consumable list of dicts."""
    spaces_raw = client.list_spaces()
    spaces = []
    for space_raw in spaces_raw:
        space = {
            "name": space_raw["name"],
            "subnets": [subnet_raw["cidr"] for subnet_raw in space_raw["subnets"]],
        }
        spaces.append(space)
    return spaces


def map_space(
    deployments_config: DeploymentsConfig,
    client: MaasClient,
    space: str,
    network: Networks,
):
    """Map space to network."""
    spaces_raw = client.list_spaces()
    for space_raw in spaces_raw:
        if space_raw["name"] == space:
            break
    else:
        raise ValueError(f"Space {space!r} not found.")

    deployment = deployments_config.get_active()
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    deployment.network_mapping[network.value] = space
    deployments_config.write()


def unmap_space(deployments_config: DeploymentsConfig, network: Networks):
    """Unmap network."""
    deployment = deployments_config.get_active()
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    deployment.network_mapping.pop(network.value, None)
    deployments_config.write()


@overload
def get_network_mapping(deployment: MaasDeployment) -> dict[str, str | None]:
    pass


@overload
def get_network_mapping(deployment: DeploymentsConfig) -> dict[str, str | None]:
    pass


def get_network_mapping(
    deployment: MaasDeployment | DeploymentsConfig,
) -> dict[str, str | None]:
    """Return network mapping."""
    if isinstance(deployment, DeploymentsConfig):
        dep = deployment.get_active()
    else:
        dep = deployment
    if not is_maas_deployment(dep):
        raise ValueError(f"Deployment {dep.name} is not a MAAS deployment.")
    mapping = dep.network_mapping.copy()
    for network in Networks:
        mapping.setdefault(network.value, None)
    return mapping


ROLES_NEEDED_ERROR = f"""A machine needs roles to be a part of an openstack deployment.
Available roles are: {RoleTags.values()}.
Roles can be assigned to a machine by applying tags in MAAS.
More on assigning tags: https://maas.io/docs/using-machine-tags
"""


class AddMaasDeployment(BaseStep):
    def __init__(
        self,
        deployments_config: DeploymentsConfig,
        deployment: str,
        token: str,
        url: str,
        resource_pool: str,
    ) -> None:
        super().__init__(
            "Add MAAS-backed deployment",
            "Adding MAAS-backed deployment for OpenStack usage",
        )
        self.deployments_config = deployments_config
        self.deployment = deployment
        self.token = token
        self.url = url
        self.resource_pool = resource_pool

    def is_skip(self, status: Status | None = None) -> Result:
        """Check if deployment is already added."""
        for deployment in self.deployments_config.deployments:
            if deployment.name == self.deployment:
                return Result(
                    ResultType.FAILED, f"Deployment {self.deployment} already exists."
                )

        current_deployments = set()
        for deployment in self.deployments_config.deployments:
            if is_maas_deployment(deployment):
                current_deployments.add(
                    (
                        deployment.url,
                        deployment.resource_pool,
                    )
                )

        if (self.url, self.resource_pool) in current_deployments:
            return Result(
                ResultType.FAILED,
                "Deployment with same url and resource pool already exists.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Check MAAS is working, Resource Pool exists, write to local configuration."""
        try:
            client = MaasClient(self.url, self.token)
            _ = client.get_resource_pool(self.resource_pool)
        except ValueError as e:
            LOG.debug("Failed to connect to maas", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        except bones.CallError as e:
            if e.status == 401:
                LOG.debug("Unauthorized", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    "Unauthorized, check your api token has necessary permissions.",
                )
            elif e.status == 404:
                LOG.debug("Resource pool not found", exc_info=True)
                return Result(
                    ResultType.FAILED,
                    f"Resource pool {self.resource_pool!r} not"
                    " found in given MAAS URL.",
                )
            LOG.debug("Unknown error", exc_info=True)
            return Result(ResultType.FAILED, f"Unknown error, {e}")
        except Exception as e:
            match type(e.__cause__):
                case builtins.ConnectionRefusedError:
                    LOG.debug("Connection refused", exc_info=True)
                    return Result(
                        ResultType.FAILED, "Connection refused, is the url correct?"
                    )
                case ssl.SSLError:
                    LOG.debug("SSL error", exc_info=True)
                    return Result(
                        ResultType.FAILED, "SSL error, failed to connect to remote."
                    )
            LOG.info("Exception info", exc_info=True)
            return Result(ResultType.FAILED, str(e))
        data = MaasDeployment(
            name=self.deployment,
            token=self.token,
            url=self.url,
            resource_pool=self.resource_pool,
            network_mapping={},
            juju_account=None,
            juju_controller=None,
        )
        self.deployments_config.add_deployment(data)
        return Result(ResultType.COMPLETED)


class MachineRolesCheck(DiagnosticsCheck):
    """Check machine has roles assigned."""

    def __init__(self, machine: dict):
        super().__init__(
            "Role check",
            "Checking roles",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult(
                self.name,
                False,
                "machine has no role assigned.",
                diagnostics=ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult(
            self.name,
            True,
            ", ".join(self.machine["roles"]),
            machine=self.machine["hostname"],
        )


class MachineNetworkCheck(DiagnosticsCheck):
    """Check machine has the right networks assigned."""

    def __init__(self, deployment: MaasDeployment, machine: dict):
        super().__init__(
            "Network check",
            "Checking networks",
        )
        self.deployment = deployment
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has access to required networks."""
        network_to_space_mapping = get_network_mapping(self.deployment)
        spaces = network_to_space_mapping.values()
        if len(spaces) != len(Networks.values()) or not all(spaces):
            return DiagnosticsResult.fail(
                self.name,
                "network mapping is incomplete",
                diagnostics=textwrap.dedent(
                    """\
                    A complete map of networks to spaces is required to proceed.
                    Complete network mapping to using `sunbeam deployment space map...`.
                    """
                ),
                machine=self.machine["hostname"],
            )
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned",
                diagnostics=ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )
        assigned_spaces = self.machine["spaces"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned spaces: {assigned_spaces!r}")
        required_networks: set[Networks] = set()
        for role in assigned_roles:
            required_networks.update(ROLE_NETWORK_MAPPING[RoleTags(role)])
        LOG.debug(
            f"{self.machine['hostname']=!r} required networks: {required_networks!r}"
        )
        required_spaces = set()
        missing_spaces = set()
        for network in required_networks:
            corresponding_space = network_to_space_mapping[network.value]
            required_spaces.add(corresponding_space)
            if corresponding_space not in assigned_spaces:
                missing_spaces.add(corresponding_space)
        LOG.debug(f"{self.machine['hostname']=!r} missing spaces: {missing_spaces!r}")
        if not assigned_spaces or missing_spaces:
            return DiagnosticsResult.fail(
                self.name,
                f"missing {', '.join(missing_spaces)}",
                diagnostics=textwrap.dedent(
                    f"""\
                    A machine needs to be in spaces to be a part of an openstack
                    deployment. Given machine has roles: {', '.join(assigned_roles)},
                    and therefore needs to be a part of the following spaces:
                    {', '.join(required_spaces)}."""
                ),
                machine=self.machine["hostname"],
            )
        return DiagnosticsResult.success(
            self.name,
            ", ".join(assigned_spaces),
            machine=self.machine["hostname"],
        )


class MachineStorageCheck(DiagnosticsCheck):
    """Check machine has storage assigned if required."""

    def __init__(self, machine: dict):
        super().__init__(
            "Storage check",
            "Checking storage",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has storage assigned if required."""
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned.",
                ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )
        if RoleTags.STORAGE.value not in assigned_roles:
            self.message = "not a storage node."
            return DiagnosticsResult.success(
                self.name,
                self.message,
                machine=self.machine["hostname"],
            )
        # TODO(gboutry): check number of storage ?
        if self.machine["storage"].get(StorageTags.CEPH.value, 0) < 1:
            return DiagnosticsResult.fail(
                self.name,
                "storage node has no ceph storage",
                textwrap.dedent(
                    f"""\
                    A storage node needs to have ceph storage to be a part of
                    an openstack deployment. Either add ceph storage to the
                    machine or remove the storage role. Add the tag
                    `{StorageTags.CEPH.value}` to the storage device in MAAS.
                    More on assigning tags: https://maas.io/docs/using-storage-tags"""
                ),
                machine=self.machine["hostname"],
            )
        return DiagnosticsResult.success(
            self.name,
            ", ".join(
                f"{tag}({count})" for tag, count in self.machine["storage"].items()
            ),
            machine=self.machine["hostname"],
        )


class MachineRequirementsCheck(DiagnosticsCheck):
    """Check machine meets requirements."""

    def __init__(self, machine: dict):
        super().__init__(
            "Machine requirements check",
            "Checking machine requirements",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine meets requirements."""
        if [RoleTags.JUJU_CONTROLLER.value] == self.machine["roles"]:
            memory_min = RAM_4_GB_IN_MB
            core_min = 2
        else:
            memory_min = RAM_32_GB_IN_MB
            core_min = 16
        if self.machine["memory"] < memory_min or self.machine["cores"] < core_min:
            return DiagnosticsResult.fail(
                self.name,
                "machine does not meet requirements",
                textwrap.dedent(
                    f"""\
                    A machine needs to have at least {core_min} cores and
                    {memory_min}MB RAM to be a part of an openstack deployment.
                    Either add more cores and memory to the machine or remove the
                    machine from the deployment.
                    {self.machine['hostname']}:
                        roles: {self.machine["roles"]}
                        cores: {self.machine["cores"]}
                        memory: {self.machine["memory"]}MB"""
                ),
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult.success(
            self.name,
            f"{self.machine['cores']} cores, {self.machine['memory']}MB RAM",
            machine=self.machine["hostname"],
        )


def str_presenter(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """Return multiline string as '|' literal block.

    Ref: https://stackoverflow.com/questions/8640959/how-can-i-control-what-scalar-form-pyyaml-uses-for-my-data # noqa E501
    """
    if data.count("\n") > 0:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _run_check_list(checks: list[DiagnosticsCheck]) -> list[DiagnosticsResult]:
    check_results = []
    for check in checks:
        LOG.debug(f"Starting check {check.name}")
        results = check.run()
        if isinstance(results, DiagnosticsResult):
            results = [results]
        for result in results:
            LOG.debug(f"{result.name=!r}, {result.passed=!r}, {result.message=!r}")
            check_results.extend(results)
    return check_results


class DeploymentMachinesCheck(DiagnosticsCheck):
    """Check all machines inside deployment."""

    def __init__(self, deployment: MaasDeployment, machines: list[dict]):
        super().__init__(
            "Deployment check",
            "Checking machines, roles, networks and storage",
        )
        self.deployment = deployment
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a series of checks on the machines' definition."""
        checks = []
        for machine in self.machines:
            checks.append(MachineRolesCheck(machine))
            checks.append(MachineNetworkCheck(self.deployment, machine))
            checks.append(MachineStorageCheck(machine))
            checks.append(MachineRequirementsCheck(machine))
        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, all(result.passed for result in results))
        )
        return results


class DeploymentRolesCheck(DiagnosticsCheck):
    """Check deployment as enough nodes with given role."""

    def __init__(
        self, machines: list[dict], role_name: str, role_tag: str, min_count: int = 3
    ):
        super().__init__(
            "Minimum role check",
            "Checking minimum number of machines with given role",
        )
        self.machines = machines
        self.role_name = role_name
        self.role_tag = role_tag
        self.min_count = min_count

    def run(self) -> DiagnosticsResult:
        """Checks if there's enough machines with given role."""
        machines = 0
        for machine in self.machines:
            if self.role_tag in machine["roles"]:
                machines += 1
        if machines < self.min_count:
            return DiagnosticsResult.fail(
                self.name,
                "less than 3 " + self.role_name,
                textwrap.dedent(
                    """\
                    A deployment needs to have at least {min_count} {role_name} to be
                    a part of an openstack deployment. You need to add more {role_name}
                    to the deployment using {role_tag} tag.
                    More on using tags: https://maas.io/docs/using-machine-tags
                    """.format(
                        min_count=self.min_count,
                        role_name=self.role_name,
                        role_tag=self.role_tag,
                    )
                ),
            )
        return DiagnosticsResult.success(
            self.name,
            f"{self.role_name}: {machines}",
        )


class ZonesCheck(DiagnosticsCheck):
    """Check that there either 1 zone or more than 2 zones."""

    def __init__(self, zones: list[str]):
        super().__init__(
            "Zone check",
            "Checking zones",
        )
        self.zones = zones

    def run(self) -> DiagnosticsResult:
        """Checks deployment zones."""
        if len(self.zones) == 2:
            return DiagnosticsResult.fail(
                self.name,
                "deployment has 2 zones",
                textwrap.dedent(
                    f"""\
                    A deployment needs to have either 1 zone or more than 2 zones.
                    Current zones: {', '.join(self.zones)}"""
                ),
            )
        return DiagnosticsResult.success(
            self.name,
            f"{len(self.zones)} zone(s)",
        )


class ZoneBalanceCheck(DiagnosticsCheck):
    """Check that roles are balanced throughout zones."""

    def __init__(self, machines: dict[str, list[dict]]):
        super().__init__(
            "Zone balance check",
            "Checking role distribution across zones",
        )
        self.machines = machines

    def run(self) -> DiagnosticsResult:
        """Check role distribution across zones."""
        zone_role_counts = {}
        for zone, machines in self.machines.items():
            zone_role_counts.setdefault(zone, {})
            for machine in machines:
                for role in machine["roles"]:
                    zone_role_counts[zone].setdefault(role, 0)
                    zone_role_counts[zone][role] += 1
        LOG.debug(f"{zone_role_counts=!r}")
        unbalanced_roles = []
        distribution = ""
        for role in RoleTags.values():
            counts = [zone_role_counts[zone].get(role, 0) for zone in zone_role_counts]
            max_count = max(counts)
            min_count = min(counts)
            if max_count != min_count:
                unbalanced_roles.append(role)
            distribution += f"{role}:"
            for zone, counts in zone_role_counts.items():
                distribution += f"\n  {zone}={counts.get(role, 0)}"
            distribution += "\n"

        if unbalanced_roles:
            diagnostics = textwrap.dedent(
                """\
                A deployment needs to have the same number of machines with the same
                role in each zone. Either add more machines to the zones or remove the
                zone from the deployment.
                More on using tags: https://maas.io/docs/using-machine-tags
                Distribution of roles across zones:
                """
            )
            diagnostics += distribution
            return DiagnosticsResult.fail(
                self.name,
                f"{', '.join(unbalanced_roles)} distribution is unbalanced",
                diagnostics,
            )
        return DiagnosticsResult.success(
            self.name,
            "deployment is balanced",
            distribution,
        )


class DeploymentTopologyCheck(DiagnosticsCheck):
    """Check deployment topology."""

    def __init__(self, machines: list[dict]):
        super().__init__(
            "Topology check",
            "Checking zone distribution",
        )
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a sequence of checks to validate deployment topology.""" ""
        machines_by_zone = _group_machines_by_zone(self.machines)
        checks = []
        checks.append(
            DeploymentRolesCheck(
                self.machines, "juju controllers", RoleTags.JUJU_CONTROLLER.value
            )
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "control nodes", RoleTags.CONTROL.value)
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "compute nodes", RoleTags.COMPUTE.value)
        )
        checks.append(
            DeploymentRolesCheck(self.machines, "storage nodes", RoleTags.STORAGE.value)
        )
        checks.append(ZonesCheck(list(machines_by_zone.keys())))
        checks.append(ZoneBalanceCheck(machines_by_zone))

        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, all(result.passed for result in results))
        )
        return results


class NetworkMappingCompleteCheck(Check):
    """Check network mapping is complete."""

    def __init__(self, deployment: MaasDeployment):
        super().__init__(
            "NetworkMapping Check",
            "Checking network mapping is complete",
        )
        self.deployment = deployment

    def run(self) -> bool:
        """Check network mapping is complete."""
        network_to_space_mapping = self.deployment.network_mapping
        spaces = network_to_space_mapping.values()
        if len(spaces) != len(Networks.values()) or not all(spaces):
            self.message = (
                "A complete map of networks to spaces is required to proceed."
                " Complete network mapping to using `sunbeam deployment space map...`."
            )
            return False
        return True


class MaasBootstrapJujuStep(BootstrapJujuStep):
    """Bootstrap the Juju controller."""

    def __init__(
        self,
        cloud: str,
        cloud_type: str,
        controller: str,
        password: str,
        bootstrap_args: list[str] | None = None,
        preseed_file: Path | None = None,
        accept_defaults: bool = False,
    ):
        bootstrap_args = bootstrap_args or []
        bootstrap_args.extend(
            (
                "--bootstrap-constraints",
                f"tags={RoleTags.JUJU_CONTROLLER.value}",
                "--bootstrap-base",
                "ubuntu@22.04",
                "--config",
                f"admin-secret={password}",
                "--debug",
            )
        )
        super().__init__(
            # client is not used when bootstrapping with maas, as it was used during prompts
            # and there's no prompt with maas
            None, # type: ignore
            cloud,
            cloud_type,
            controller,
            bootstrap_args,
            preseed_file,
            accept_defaults,
        )

    def prompt(self, console: Console | None = None) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
        return False


class MaasScaleJujuStep(ScaleJujuStep):
    """Scale Juju Controller on MAAS deployment."""

    def __init__(
        self,
        maas_client: MaasClient,
        controller: str,
        extra_args: list[str] | None = None,
    ):
        extra_args = extra_args or []
        extra_args.extend(
            (
                "--constraints",
                f"tags={RoleTags.JUJU_CONTROLLER.value}",
            )
        )
        super().__init__(controller, extra_args=extra_args)
        self.client = maas_client

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            controller = self.get_controller(self.controller)
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        controller_machines = controller.get("controller-machines")
        if controller_machines is None:
            return Result(
                ResultType.FAILED,
                f"Controller {self.controller} has no machines registered.",
            )
        nb_controllers = len(controller_machines)

        if nb_controllers == self.n:
            LOG.debug("Already the correct number of controllers, skipping scaling...")
            return Result(ResultType.SKIPPED)

        if nb_controllers > self.n:
            return Result(
                ResultType.FAILED,
                f"Can't scale down controllers from {nb_controllers} to {self.n}.",
            )

        machines = list_machines(self.client, tags=RoleTags.JUJU_CONTROLLER.value)

        if len(machines) < self.n:
            LOG.debug(
                f"Found {len(machines)} juju controllers,"
                f" need {self.n} to scale, skipping..."
            )
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)


class MaasSaveControllerStep(BaseStep, JujuStepHelper):
    """Save maas controller information locally."""

    def __init__(
        self,
        controller: str,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
    ):
        super().__init__(
            "Save controller information",
            "Saving controller information locally",
        )
        self.controller = controller
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config

    def _get_controller(self, name: str) -> JujuController | None:
        try:
            controller = self.get_controller(name)["details"]
        except ControllerNotFoundException as e:
            LOG.debug(str(e))
            return None
        return JujuController(
            api_endpoints=controller["api-endpoints"],
            ca_cert=controller["ca-cert"],
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.SKIPPED)
        if deployment.juju_controller is None:
            return Result(ResultType.COMPLETED)

        controller = self._get_controller(self.controller)
        if controller is None:
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        if controller == deployment.juju_controller:
            return Result(ResultType.SKIPPED)

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Save controller to deployment information."""
        controller = self._get_controller(self.controller)
        if controller is None:
            return Result(ResultType.FAILED, f"Controller {self.controller} not found")

        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not is_maas_deployment(deployment):
            return Result(ResultType.FAILED)

        deployment.juju_controller = controller
        self.deployments_config.write()
        return Result(ResultType.COMPLETED)