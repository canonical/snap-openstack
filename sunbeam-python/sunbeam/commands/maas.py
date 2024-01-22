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
from typing import Optional, TypeGuard

import yaml
from maas.client import bones, connect
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

from sunbeam.commands.deployment import (
    Deployment,
    DeploymentType,
    add_deployment,
    deployment_config,
    deployment_path,
    get_active_deployment,
    update_deployment,
)
from sunbeam.jobs.checks import DiagnosticsCheck, DiagnosticsResult
from sunbeam.jobs.common import RAM_32_GB_IN_MB, BaseStep, Result, ResultType

LOG = logging.getLogger(__name__)
console = Console()

MAAS_CONFIG = "maas.yaml"


class MaasDeployment(Deployment):
    token: str
    resource_pool: str
    network_mapping: dict[str, str | None]


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

    def __repr__(self) -> str:
        return self.value


def is_maas_deployment(deployment: Deployment) -> TypeGuard[MaasDeployment]:
    """Check if deployment is a MAAS deployment."""
    return deployment["type"] == DeploymentType.MAAS.value


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

    def __init__(self, url: str, token: str, resource_pool: Optional[str] = None):
        self._client = connect(url, apikey=token)
        self.resource_pool = resource_pool

    def get_resource_pool(self, name: str) -> object:
        """Fetch resource pool from MAAS."""
        return self._client.resource_pools.get(name)  # type: ignore

    def list_machines(self) -> list[dict]:
        """List machines."""
        kwargs = {}
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
    def active(cls, snap: Snap) -> "MaasClient":
        """Return client connected to active deployment."""
        path = deployment_path(snap)
        deployment = get_active_deployment(path)
        if not is_maas_deployment(deployment):
            raise ValueError("Active deployment is not a MAAS deployment.")
        return cls(
            deployment["url"],
            deployment["token"],
            deployment["resource_pool"],
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


def list_machines(client: MaasClient) -> list[dict]:
    """List machines in deployment, return consumable list of dicts."""
    machines_raw = client.list_machines()

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


def map_space(snap: Snap, client: MaasClient, space: str, network: str):
    """Map space to network."""
    if network not in Networks.values():
        raise ValueError(f"Network {network!r} is not a valid network.")

    spaces_raw = client.list_spaces()
    for space_raw in spaces_raw:
        if space_raw["name"] == space:
            break
    else:
        raise ValueError(f"Space {space!r} not found.")

    path = deployment_path(snap)
    deployment = get_active_deployment(path)
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    network_mapping = deployment.get("network_mapping", {})
    network_mapping[network] = space
    deployment["network_mapping"] = network_mapping

    update_deployment(path, deployment)


def unmap_space(snap: Snap, network: str):
    """Unmap network."""
    if network not in Networks.values():
        raise ValueError(f"Network {network!r} is not a valid network.")

    path = deployment_path(snap)
    deployment = get_active_deployment(path)
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    network_mapping = deployment.get("network_mapping", {})
    network_mapping.pop(network, None)
    deployment["network_mapping"] = network_mapping

    update_deployment(path, deployment)


def get_network_mapping(snap: Snap) -> dict[str, str | None]:
    """Return network mapping."""
    path = deployment_path(snap)
    deployment = get_active_deployment(path)
    if not is_maas_deployment(deployment):
        raise ValueError("Active deployment is not a MAAS deployment.")
    mapping = deployment.get("network_mapping", {})
    for network in Networks.values():
        mapping.setdefault(network, None)
    return mapping


ROLES_NEEDED_ERROR = f"""A machine needs roles to be a part of an openstack deployment.
Available roles are: {RoleTags.values()}.
Roles can be assigned to a machine by applying tags in MAAS.
More on assigning tags: https://maas.io/docs/using-machine-tags
"""


class AddMaasDeployment(BaseStep):
    def __init__(
        self,
        deployment: str,
        token: str,
        url: str,
        resource_pool: str,
        config_path: Path,
    ) -> None:
        super().__init__(
            "Add MAAS-backed deployment",
            "Adding MAAS-backed deployment for OpenStack usage",
        )
        self.deployment = deployment
        self.token = token
        self.url = url
        self.resource_pool = resource_pool
        self.path = config_path

    def is_skip(self, status: Optional[Status] = None) -> Result:
        """Check if deployment is already added."""
        config = deployment_config(self.path)
        if self.deployment in config:
            return Result(
                ResultType.FAILED, f"Deployment {self.deployment} already exists."
            )

        current_deployments = set()
        for deployment in config.get("deployments", []):
            if is_maas_deployment(deployment):
                current_deployments.add(
                    (
                        deployment["url"],
                        deployment["resource_pool"],
                    )
                )

        if (self.url, self.resource_pool) in current_deployments:
            return Result(
                ResultType.FAILED,
                "Deployment with same url and resource pool already exists.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Optional[Status] = None) -> Result:
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
            type=DeploymentType.MAAS.value,
            resource_pool=self.resource_pool,
            network_mapping={},
        )
        add_deployment(self.path, data)
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

    def __init__(self, snap: Snap, machine: dict):
        super().__init__(
            "Network check",
            "Checking networks",
        )
        self.snap = snap
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has access to required networks."""
        network_to_space_mapping = get_network_mapping(self.snap)
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

    CORES = 16
    MEMORY = RAM_32_GB_IN_MB

    def __init__(self, machine: dict):
        super().__init__(
            "Machine requirements check",
            "Checking machine requirements",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        if self.machine["memory"] < self.MEMORY or self.machine["cores"] < self.CORES:
            return DiagnosticsResult.fail(
                self.name,
                "machine does not meet requirements",
                textwrap.dedent(
                    f"""\
                    A machine needs to have at least {self.CORES} cores and
                    {self.MEMORY}MB RAM to be a part of an openstack deployment.
                    Either add more cores and memory to the machine or remove the
                    machine from the deployment.
                    {self.machine['hostname']}:
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

    def __init__(self, snap: Snap, machines: list[dict]):
        super().__init__(
            "Deployment check",
            "Checking machines, roles, networks and storage",
        )
        self.snap = snap
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a series of checks on the machines' definition."""
        checks = []
        for machine in self.machines:
            checks.append(MachineRolesCheck(machine))
            checks.append(MachineNetworkCheck(self.snap, machine))
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

    def __init__(self, snap: Snap, machines: list[dict]):
        super().__init__(
            "Topology check",
            "Checking zone distribution",
        )
        self.snap = snap
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
