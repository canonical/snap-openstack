# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import ast
import builtins
import collections
import copy
import ipaddress
import json
import logging
import re
import ssl
import textwrap
import typing
from pathlib import Path
from typing import Sequence, Tuple

import tenacity
from rich.console import Console
from rich.status import Status
from snaphelpers import Snap

import sunbeam.core.questions
import sunbeam.provider.maas.client as maas_client
import sunbeam.provider.maas.deployment as maas_deployment
import sunbeam.steps.k8s as k8s
import sunbeam.steps.microceph as microceph
import sunbeam.utils as sunbeam_utils
from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import NodeNotExistInClusterException
from sunbeam.commands.configure import (
    CLOUD_CONFIG_SECTION,
    PCI_CONFIG_SECTION,
    VARIABLE_DEFAULTS,
    BaseConfigDPDKStep,
    SetHypervisorUnitsOptionsStep,
    ext_net_questions,
)
from sunbeam.core.checks import Check, DiagnosticsCheck, DiagnosticsResult
from sunbeam.core.common import (
    RAM_4_GB_IN_MB,
    RAM_32_GB_IN_MB,
    BaseStep,
    Result,
    ResultType,
    parse_ip_range,
)
from sunbeam.core.deployment import CertPair, Networks
from sunbeam.core.deployments import DeploymentsConfig
from sunbeam.core.juju import (
    ActionFailedException,
    JujuHelper,
    JujuSecretNotFound,
    JujuStepHelper,
    LeaderNotFoundException,
    UnitNotFoundException,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.steps import CreateLoadBalancerIPPoolsStep
from sunbeam.core.terraform import TerraformHelper
from sunbeam.lazy import LazyImport
from sunbeam.provider.common import nic_utils
from sunbeam.steps import clusterd
from sunbeam.steps.cluster_status import ClusterStatusStep
from sunbeam.steps.clusterd import APPLICATION as CLUSTERD_APPLICATION
from sunbeam.steps.juju import (
    JUJU_CONTROLLER_CHARM,
    BootstrapJujuStep,
    ControllerNotFoundException,
    SaveControllerStep,
    ScaleJujuStep,
)
from sunbeam.steps.openstack import EndpointsConfigurationStep
from sunbeam.versions import JUJU_BASE

if typing.TYPE_CHECKING:
    from maas.client import bones  # type: ignore [import-untyped]
else:
    bones = LazyImport("maas.client.bones")

LOG = logging.getLogger(__name__)
console = Console()

ROLES_NEEDED_ERROR = f"""A machine needs roles to be a part of an openstack deployment.
Available roles are: {maas_deployment.RoleTags.values()}.
Roles can be assigned to a machine by applying tags in MAAS.
More on assigning tags: https://maas.io/docs/how-to-use-machine-tags
"""
MACHINE_DEPLOY_TIMEOUT = 3600


class AddMaasDeployment(BaseStep):
    """Perform various checks and add MAAS-backed deployment."""

    def __init__(
        self,
        deployments_config: DeploymentsConfig,
        deployment: maas_deployment.MaasDeployment,
    ) -> None:
        super().__init__(
            "Add MAAS-backed deployment",
            "Adding MAAS-backed deployment for OpenStack usage",
        )
        self.deployments_config = deployments_config
        self.deployment = deployment

    def is_skip(self, status: Status | None = None) -> Result:
        """Check if deployment is already added."""
        try:
            self.deployments_config.get_deployment(self.deployment.name)
            return Result(
                ResultType.FAILED, f"Deployment {self.deployment.name} already exists."
            )
        except ValueError:
            pass

        current_deployments = set()
        for deployment in self.deployments_config.deployments:
            if maas_deployment.is_maas_deployment(deployment):
                current_deployments.add(
                    (
                        deployment.url,
                        deployment.resource_tag,
                    )
                )

        if (self.deployment.url, self.deployment.resource_tag) in current_deployments:
            return Result(
                ResultType.FAILED,
                "Deployment with same url and resource tag already exists.",
            )

        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Check MAAS is working, write to local configuration."""
        try:
            client = maas_client.MaasClient(self.deployment.url, self.deployment.token)
            client.ensure_tag(self.deployment.resource_tag)
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

        spaces = {
            space
            for space in self.deployment.network_mapping.values()
            if space is not None
        }
        if len(spaces) > 0:
            try:
                maas_spaces = maas_client.list_spaces(client)
            except ValueError as e:
                LOG.debug("Failed to list spaces", exc_info=True)
                return Result(ResultType.FAILED, str(e))
            maas_unique_spaces: set[str] = {
                maas_space["name"] for maas_space in maas_spaces
            }
            difference = spaces.difference(maas_unique_spaces)
            if len(difference) > 0:
                return Result(
                    ResultType.FAILED,
                    f"Spaces {', '.join(difference)} not found in MAAS.",
                )

        if (
            self.deployment.juju_controller is None
            and self.deployment.juju_account is not None
        ):
            return Result(
                ResultType.FAILED,
                "Juju account configured, but Juju Controller not configured.",
            )

        if (
            self.deployment.juju_controller is not None
            and self.deployment.juju_account is None
        ):
            return Result(
                ResultType.FAILED,
                "Juju Controller configured, but Juju account not configured.",
            )

        if (
            self.deployment.juju_account is not None
            and self.deployment.juju_controller is not None
        ):
            jhelper = JujuHelper(self.deployment.juju_controller)
            try:
                jhelper.models()
            except Exception as e:
                LOG.debug("Failed to list models", exc_info=True)
                return Result(ResultType.FAILED, str(e))

        self.deployments_config.add_deployment(self.deployment)
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
        """List machines roles, fail if machine is missing roles."""
        super().run
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned.",
                diagnostics=ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult.success(
            self.name,
            ", ".join(self.machine["roles"]),
            machine=self.machine["hostname"],
        )


class MachineNetworkCheck(DiagnosticsCheck):
    """Check machine has the right networks assigned."""

    def __init__(self, deployment: maas_deployment.MaasDeployment, machine: dict):
        super().__init__(
            "Network check",
            "Checking networks",
        )
        self.deployment = deployment
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has access to required networks."""
        network_to_space_mapping = maas_client.get_network_mapping(self.deployment)
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
            required_networks.update(
                maas_deployment.ROLE_NETWORK_MAPPING[maas_deployment.RoleTags(role)]
            )
        LOG.debug(
            f"{self.machine['hostname']=!r} required networks: {required_networks!r}"
        )
        required_spaces: set[str] = set()
        missing_spaces: set[str] = set()
        for network in required_networks:
            corresponding_space = network_to_space_mapping[network.value]
            if not corresponding_space:
                LOG.debug(f"{network.value=!r} has no corresponding space")
                continue
            required_spaces.add(corresponding_space)
            if corresponding_space and corresponding_space not in assigned_spaces:
                missing_spaces.add(corresponding_space)
        LOG.debug(f"{self.machine['hostname']=!r} missing spaces: {missing_spaces!r}")
        if not assigned_spaces or missing_spaces:
            return DiagnosticsResult.fail(
                self.name,
                f"missing {', '.join(missing_spaces)}",
                diagnostics=textwrap.dedent(
                    f"""\
                    A machine needs to be in spaces to be a part of an openstack
                    deployment. Given machine has roles: {", ".join(assigned_roles)},
                    and therefore needs to be a part of the following spaces:
                    {", ".join(required_spaces)}."""
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
        if maas_deployment.RoleTags.STORAGE.value not in assigned_roles:
            self.message = "not a storage node."
            return DiagnosticsResult.success(
                self.name,
                self.message,
                machine=self.machine["hostname"],
            )
        # TODO(gboutry): check number of storage ?
        ceph_storage = self.machine["storage"].get(
            maas_deployment.StorageTags.CEPH.value, []
        )
        if len(ceph_storage) < 1:
            return DiagnosticsResult.fail(
                self.name,
                "storage node has no ceph storage",
                textwrap.dedent(
                    f"""\
                    A storage node needs to have ceph storage to be a part of
                    an openstack deployment. Either add ceph storage to the
                    machine or remove the storage role. Add the tag
                    `{maas_deployment.StorageTags.CEPH.value}` to the storage device in\
                     MAAS.
                    More on assigning tags:
                    https://maas.io/docs/how-to-use-storage-tags"""
                ),
                machine=self.machine["hostname"],
            )
        return DiagnosticsResult.success(
            self.name,
            ", ".join(
                f"{tag}({len(devices)})"
                for tag, devices in self.machine["storage"].items()
            ),
            machine=self.machine["hostname"],
        )


class MachineComputeNicCheck(DiagnosticsCheck):
    """Check machine has compute nic assigned if required."""

    def __init__(self, machine: dict):
        super().__init__(
            "Compute Nic check",
            "Checking compute nic",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine has compute nic if required."""
        assigned_roles = self.machine["roles"]
        LOG.debug(f"{self.machine['hostname']=!r} assigned roles: {assigned_roles!r}")
        if not assigned_roles:
            return DiagnosticsResult.fail(
                self.name,
                "machine has no role assigned.",
                ROLES_NEEDED_ERROR,
                machine=self.machine["hostname"],
            )
        compute_tag = maas_deployment.NicTags.COMPUTE.value
        if maas_deployment.RoleTags.COMPUTE.value not in assigned_roles:
            self.message = "not a compute node."
            return DiagnosticsResult.success(
                self.name,
                self.message,
                machine=self.machine["hostname"],
            )
        nics = self.machine["nics"]
        for nic in nics:
            if compute_tag in nic["tags"]:
                return DiagnosticsResult.success(
                    self.name,
                    compute_tag + " nic found",
                    machine=self.machine["hostname"],
                )

        return DiagnosticsResult.fail(
            self.name,
            "no compute nic found",
            textwrap.dedent(
                f"""\
                A compute node needs to have a dedicated nic for compute to be a part
                of an openstack deployment. Either add a compute nic to the machine or
                remove the compute role. Add the tag `{compute_tag}`
                to the nic in MAAS.
                More on assigning tags: https://maas.io/docs/how-to-use-network-tags
                """
            ),
            machine=self.machine["hostname"],
        )


class MachineRootDiskCheck(DiagnosticsCheck):
    """Check machine's root disk is a SSD and is large enough."""

    def __init__(self, machine: dict):
        super().__init__(
            "Root disk check",
            "Checking root disk",
        )
        self.machine = machine

    def run(self) -> DiagnosticsResult:
        """Check machine's root disk is a SSD and is large enough."""
        root_disk = self.machine.get("root_disk")

        if root_disk is None:
            return DiagnosticsResult.fail(
                self.name,
                "could not determine root disk",
                "A machine needs to have a root disk to be a"
                " part of an openstack deployment.",
                machine=self.machine["hostname"],
            )

        root_partition = root_disk.get("root_partition")
        if root_partition is None:
            return DiagnosticsResult.fail(
                self.name,
                "could not determine root partition",
                "A machine needs to have a root partition to be a"
                " part of an openstack deployment.",
                machine=self.machine["hostname"],
            )

        physical_devices = root_disk.get("physical_blockdevices", ())
        virtual_device = root_disk.get("virtual_blockdevice")
        if not physical_devices:
            if virtual_device:
                disk_msg = "Detected root disk to be a virtual device."
            else:
                disk_msg = "Could not deternine root disk to be a physical device."
            return DiagnosticsResult.warn(
                self.name,
                "could not determine physical devices",
                disk_msg
                + " A machine root disk needs to be be backed by physical devices"
                " be a part of an openstack deployment.",
                machine=self.machine["hostname"],
            )

        if any("ssd" not in device.get("tags", ()) for device in physical_devices):
            if virtual_device:
                disk_msg = (
                    "Detected root disk to be a virtual device backed by"
                    " physical devices that are not all SSDs."
                )
            else:
                disk_msg = (
                    "Detected root disk to be backed by physical devices that"
                    " are not all SSDs."
                )
            return DiagnosticsResult.warn(
                self.name,
                "root disk is not a SSD",
                disk_msg + " A machine root disk needs to be an SSD to be"
                " a part of an openstack deployment. Deploying "
                "without SSD root disk can lead to performance issues.",
                machine=self.machine["hostname"],
            )

        if root_partition.get("size", 0) < 500 * 1024**3:
            return DiagnosticsResult.warn(
                self.name,
                "root disk is too small",
                "A machine root disk needs to be at least 500GB"
                " to be a part of an openstack deployment.",
                machine=self.machine["hostname"],
            )

        return DiagnosticsResult.success(
            self.name,
            "root disk is a SSD and is large enough",
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
        if [maas_deployment.RoleTags.JUJU_CONTROLLER.value] == self.machine[
            "roles"
        ] or [maas_deployment.RoleTags.SUNBEAM.value] == self.machine["roles"]:
            memory_min = RAM_4_GB_IN_MB
            core_min = 2
        else:
            memory_min = RAM_32_GB_IN_MB
            core_min = 16
        if self.machine["memory"] < memory_min or self.machine["cores"] < core_min:
            return DiagnosticsResult.warn(
                self.name,
                "machine does not meet requirements",
                textwrap.dedent(
                    f"""\
                    A machine needs to have at least {core_min} cores and
                    {memory_min}MB RAM to be a part of an openstack deployment.
                    Either add more cores and memory to the machine or remove the
                    machine from the deployment.
                    {self.machine["hostname"]}:
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


def _run_check_list(checks: Sequence[DiagnosticsCheck]) -> list[DiagnosticsResult]:
    check_results = []
    for check in checks:
        LOG.debug(f"Starting check {check.name}")
        results = check.run()
        if isinstance(results, DiagnosticsResult):
            results = [results]
        for result in results:
            passed = result.passed.value
            LOG.debug(f"{result.name=!r}, {passed=!r}, {result.message=!r}")
            check_results.extend(results)
    return check_results


class DeploymentMachinesCheck(DiagnosticsCheck):
    """Check all machines inside deployment."""

    def __init__(
        self, deployment: maas_deployment.MaasDeployment, machines: list[dict]
    ):
        super().__init__(
            "Deployment check",
            "Checking machines, roles, networks and storage",
        )
        self.deployment = deployment
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a series of checks on the machines' definition."""
        if not self.machines:
            return [
                DiagnosticsResult.fail(
                    self.name,
                    "less than 2 machines",
                    "A deployment needs to have at least two machine"
                    " to be a part of an openstack deployment.",
                )
            ]
        checks: list[DiagnosticsCheck] = []
        for machine in self.machines:
            checks.append(MachineRolesCheck(machine))
            checks.append(MachineNetworkCheck(self.deployment, machine))
            checks.append(MachineStorageCheck(machine))
            checks.append(MachineComputeNicCheck(machine))
            checks.append(MachineRootDiskCheck(machine))
            checks.append(MachineRequirementsCheck(machine))
        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, DiagnosticsResult.coalesce_type(results))
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
        failure_diagnostics = textwrap.dedent(
            """\
            A deployment needs to have at least {min_count} {role_name} to be
            a part of an openstack deployment. You need to add more {role_name}
            to the deployment using {role_tag} tag.
            More on using tags: https://maas.io/docs/how-to-use-machine-tags
            """
        )
        if machines == 0:
            return DiagnosticsResult.fail(
                self.name,
                "no machine with role: " + self.role_name,
                failure_diagnostics.format(
                    min_count=self.min_count,
                    role_name=self.role_name,
                    role_tag=self.role_tag,
                ),
            )
        if machines < self.min_count:
            return DiagnosticsResult.warn(
                self.name,
                "less than 3 " + self.role_name,
                failure_diagnostics.format(
                    min_count=self.min_count,
                    role_name=self.role_name,
                    role_tag=self.role_tag,
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
        nb_zones = len(self.zones)
        diagnostics = textwrap.dedent(
            f"""\
            A deployment needs to have either 1 zone or more than 2 zones.
            Current zones: {", ".join(self.zones)}
            """
        )
        if nb_zones == 0:
            return DiagnosticsResult.fail(
                self.name, "deployment has no zone", diagnostics
            )
        if len(self.zones) == 2:
            return DiagnosticsResult.warn(
                self.name, "deployment has 2 zones", diagnostics
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
        zone_role_counts: dict[str, dict[str, int]] = {}
        for zone, machines in self.machines.items():
            zone_role_counts.setdefault(zone, {})
            for machine in machines:
                for role in machine["roles"]:
                    zone_role_counts[zone].setdefault(role, 0)
                    zone_role_counts[zone][role] += 1
        LOG.debug(f"{zone_role_counts=!r}")
        unbalanced_roles = []
        distribution = ""
        for role in maas_deployment.RoleTags.values():
            role_any_zone_counts = [
                zone_role_counts[zone].get(role, 0) for zone in zone_role_counts
            ]
            max_count = max(role_any_zone_counts)
            min_count = min(role_any_zone_counts)
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
                More on using tags: https://maas.io/docs/how-to-use-machine-tags
                Distribution of roles across zones:
                """
            )
            diagnostics += distribution
            return DiagnosticsResult.warn(
                self.name,
                f"{', '.join(unbalanced_roles)} distribution is unbalanced",
                diagnostics,
            )
        return DiagnosticsResult.success(
            self.name,
            "deployment is balanced",
            distribution,
        )


class IpRangesCheck(DiagnosticsCheck):
    """Check IP ranges are complete."""

    _missing_range_diagnostic = textwrap.dedent(
        """\
        IP ranges are required to proceed.
        You need to setup a Reserverd IP Range for any subnet in the
        space ({space!r}) mapped to the {network!r} network.
        Multiple ip ranges can be defined in on the same/different subnets
        in the space. Each of these ip ranges should have as comment:
        {label!r}.

        More on setting up IP ranges:
        https://maas.io/docs/how-to-manage-ip-ranges
        """
    )

    def __init__(
        self,
        client: maas_client.MaasClient,
        deployment: maas_deployment.MaasDeployment,
    ):
        super().__init__(
            "IP ranges check",
            "Checking IP ranges",
        )
        self.client = client
        self.deployment = deployment

    def _get_ranges_for_label(
        self, subnet_ranges: dict[str, list[dict[str, str]]], label: str
    ) -> list[tuple[str, str]]:
        """Ip ranges for a given label."""
        ip_ranges = []
        for ranges in subnet_ranges.values():
            for ip_range in ranges:
                if ip_range["label"] == label:
                    ip_ranges.append((ip_range["start"], ip_range["end"]))

        return ip_ranges

    def run(
        self,
    ) -> DiagnosticsResult:
        """Check Public and Internal ip ranges are set."""
        public_space = self.deployment.network_mapping.get(Networks.PUBLIC.value)
        internal_space = self.deployment.network_mapping.get(Networks.INTERNAL.value)
        if public_space is None or internal_space is None:
            return DiagnosticsResult.fail(
                self.name,
                "IP ranges are not set",
                textwrap.dedent(
                    """\
                    A complete map of networks to spaces is required to proceed.
                    Complete network mapping to using `sunbeam deployment space map...`.
                    """
                ),
            )

        public_subnet_ranges = maas_client.get_ip_ranges_from_space(
            self.client, public_space
        )
        internal_subnet_ranges = maas_client.get_ip_ranges_from_space(
            self.client, internal_space
        )

        public_ip_ranges = self._get_ranges_for_label(
            public_subnet_ranges, self.deployment.public_api_label
        )
        internal_ip_ranges = self._get_ranges_for_label(
            internal_subnet_ranges, self.deployment.internal_api_label
        )
        if len(public_ip_ranges) == 0:
            return DiagnosticsResult.fail(
                self.name,
                "Public IP ranges are not set",
                self._missing_range_diagnostic.format(
                    space=public_space,
                    network=Networks.PUBLIC.value,
                    label=self.deployment.public_api_label,
                ),
            )

        if len(internal_ip_ranges) == 0:
            return DiagnosticsResult.fail(
                self.name,
                "Internal IP ranges are not set",
                self._missing_range_diagnostic.format(
                    space=internal_space,
                    network=Networks.INTERNAL.value,
                    label=self.deployment.internal_api_label,
                ),
            )

        diagnostics = (
            f"Public IP ranges: {public_ip_ranges!r}\n"
            f"Internal IP ranges: {internal_ip_ranges!r}"
        )

        return DiagnosticsResult.success(self.name, "IP ranges are set", diagnostics)


class DeploymentTopologyCheck(DiagnosticsCheck):
    """Check deployment topology."""

    def __init__(self, machines: list[dict]):
        super().__init__(
            "Topology check",
            "Checking zone distribution",
        )
        self.machines = machines

    def run(self) -> list[DiagnosticsResult]:
        """Run a sequence of checks to validate deployment topology."""
        if len(self.machines) < 2:
            return [
                DiagnosticsResult.fail(
                    self.name,
                    "less than 2 machines",
                    "A deployment needs to have at least two machine"
                    " to be a part of an openstack deployment.",
                )
            ]
        machines_by_zone = maas_client._group_machines_by_zone(self.machines)
        checks: list[DiagnosticsCheck] = []
        if JujuStepHelper().get_external_controllers():
            LOG.info(
                "External juju controllers registered, skipping DeploymentRoleCheck "
                "for juju controllers"
            )
        else:
            checks.append(
                DeploymentRolesCheck(
                    self.machines,
                    "juju controllers",
                    maas_deployment.RoleTags.JUJU_CONTROLLER.value,
                )
            )
        checks.append(
            DeploymentRolesCheck(
                self.machines,
                "infra nodes",
                maas_deployment.RoleTags.SUNBEAM.value,
            )
        )
        checks.append(
            DeploymentRolesCheck(
                self.machines, "control nodes", maas_deployment.RoleTags.CONTROL.value
            )
        )
        checks.append(
            DeploymentRolesCheck(
                self.machines, "compute nodes", maas_deployment.RoleTags.COMPUTE.value
            )
        )
        checks.append(
            DeploymentRolesCheck(
                self.machines, "storage nodes", maas_deployment.RoleTags.STORAGE.value
            )
        )
        checks.append(ZonesCheck(list(machines_by_zone.keys())))
        checks.append(ZoneBalanceCheck(machines_by_zone))

        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, DiagnosticsResult.coalesce_type(results))
        )
        return results


class DeploymentNetworkingCheck(DiagnosticsCheck):
    """Check deployment networking."""

    def __init__(
        self,
        client: maas_client.MaasClient,
        deployment: maas_deployment.MaasDeployment,
    ):
        super().__init__(
            "Networking check",
            "Checking networking",
        )
        self.client = client
        self.deployment = deployment

    def run(self) -> list[DiagnosticsResult]:
        """Run a sequence of checks to validate deployment networking."""
        checks = []
        checks.append(IpRangesCheck(self.client, self.deployment))

        results = _run_check_list(checks)
        results.append(
            DiagnosticsResult(self.name, DiagnosticsResult.coalesce_type(results))
        )
        return results


class NetworkMappingCompleteCheck(Check):
    """Check network mapping is complete."""

    def __init__(self, deployment: maas_deployment.MaasDeployment):
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


class JujuControllerCheck(Check):
    """Check for juju controller node if required."""

    def __init__(self, deployment: maas_deployment.MaasDeployment, juju_controller):
        super().__init__(
            "Check for juju controller", "Checking if juju controller node is requred"
        )
        self.deployment = deployment
        self.controller = juju_controller
        self.maas_client = maas_client.MaasClient.from_deployment(deployment)

    def run(self) -> bool:
        """Check if juju controller is required."""
        machines = maas_client.list_machines(
            self.maas_client, tags=maas_deployment.RoleTags.JUJU_CONTROLLER.value
        )
        if self.controller and machines:
            hostnames = [m.get("hostname") for m in machines]
            self.message = (
                "WARNING: Machines with tag juju-controller not used in deployment: "
                f"{hostnames}"
            )
            LOG.warning(self.message)
            return True

        if not self.controller and not machines:
            self.message = (
                "A deployment needs to have at least 3 juju controllers to be "
                "a part of an openstack deployment. You need to add more "
                "juju-controller to the deployment using juju-controller tag."
            )
            return False

        return True


class MaasBootstrapJujuStep(BootstrapJujuStep):
    """Bootstrap the Juju controller."""

    def __init__(
        self,
        maas_client: maas_client.MaasClient,
        cloud: str,
        cloud_type: str,
        controller: str,
        password: str,
        bootstrap_args: list[str] | None = None,
        proxy_settings: dict | None = None,
    ):
        bootstrap_args = bootstrap_args or []
        bootstrap_args.extend(
            (
                "--bootstrap-constraints",
                f"tags={maas_deployment.RoleTags.JUJU_CONTROLLER.value}",
                "--bootstrap-base",
                JUJU_BASE,
            )
        )
        if proxy_settings:
            snap = Snap()
            controller_charm = str(
                snap.paths.user_common / "downloads" / JUJU_CONTROLLER_CHARM
            )
            bootstrap_args.extend(
                (
                    "--controller-charm-path",
                    controller_charm,
                )
            )
        bootstrap_args.extend(
            (
                "--config",
                f"admin-secret={password}",
            )
        )
        super().__init__(
            # client is not used when bootstrapping with maas,
            # as it was used during prompts and there's no prompt with maas
            None,  # type: ignore
            cloud,
            cloud_type,
            controller,
            bootstrap_args=bootstrap_args,
            proxy_settings=proxy_settings,
        )
        self.maas_client = maas_client

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
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

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        controller_tag = maas_deployment.RoleTags.JUJU_CONTROLLER.value
        machines = maas_client.list_machines(self.maas_client, tags=controller_tag)
        if len(machines) == 0:
            return Result(
                ResultType.FAILED,
                f"No machines with tag {controller_tag!r} found.",
            )
        controller = sorted(machines, key=lambda x: x["hostname"])[0]
        self.bootstrap_args.extend(("--to", "system-id=" + controller["system_id"]))
        return super().is_skip(status)


class MaasScaleJujuStep(ScaleJujuStep):
    """Scale Juju Controller on MAAS deployment."""

    def __init__(
        self,
        maas_client: maas_client.MaasClient,
        controller: str,
        extra_args: list[str] | None = None,
    ):
        extra_args = extra_args or []
        extra_args.extend(
            (
                "--constraints",
                f"tags={maas_deployment.RoleTags.JUJU_CONTROLLER.value}",
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

        machines = maas_client.list_machines(
            self.client, tags=maas_deployment.RoleTags.JUJU_CONTROLLER.value
        )

        if len(machines) < self.n:
            LOG.debug(
                f"Found {len(machines)} juju controllers,"
                f" need {self.n} to scale, skipping..."
            )
            return Result(ResultType.SKIPPED)
        machines = sorted(machines, key=lambda x: x["hostname"])

        system_ids = [machine["system_id"] for machine in machines]
        for controller_machine in controller_machines.values():
            if controller_machine["instance-id"] in system_ids:
                system_ids.remove(controller_machine["instance-id"])

        placement = ",".join(f"system-id={system_id}" for system_id in system_ids)

        self.extra_args.extend(("--to", placement))
        return Result(ResultType.COMPLETED)


class MaasSaveControllerStep(SaveControllerStep):
    """Save maas controller information locally."""

    def __init__(
        self,
        controller: str | None,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
        data_location: Path,
        is_external: bool = False,
    ):
        super().__init__(
            controller, deployment_name, deployments_config, data_location, is_external
        )

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        if not maas_deployment.is_maas_deployment(self.deployment):
            return Result(ResultType.SKIPPED)

        return super().is_skip(status)


class MaasSaveClusterdCredentialsStep(BaseStep):
    """Save clusterd credentials locally."""

    def __init__(
        self,
        jhelper: JujuHelper,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
    ):
        super().__init__(
            "Save clusterd credentials",
            "Saving clusterd credentials locally",
        )
        self.jhelper = jhelper
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not maas_deployment.is_maas_deployment(deployment):
            return Result(ResultType.SKIPPED)
        self.model = deployment.infra_model
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Save clusterd address to deployment information."""
        try:
            leader_unit = self.jhelper.get_leader_unit(
                CLUSTERD_APPLICATION,
                self.model,
            )
            credentials = self.jhelper.run_action(
                leader_unit, self.model, "get-credentials"
            )
        except (LeaderNotFoundException, ValueError, ActionFailedException) as e:
            return Result(ResultType.FAILED, str(e))

        url = credentials.get("url")
        if url is None:
            return Result(ResultType.FAILED, "Failed to retrieve clusterd url")

        certificate_authority = credentials.get("certificate-authority")
        certificate = credentials.get("certificate")
        private_key = None
        if private_key_secret := credentials.get("private-key-secret"):
            try:
                secret = self.jhelper.get_secret(self.model, private_key_secret)
            except JujuSecretNotFound as e:
                return Result(ResultType.FAILED, str(e))
            private_key = secret["private-key"]

        if not certificate_authority or not certificate or not private_key:
            return Result(ResultType.FAILED, "Failed to retrieve clusterd credentials")

        try:
            client = Client.from_http(
                url, certificate_authority, certificate, private_key
            )
        except ValueError:
            LOG.debug("Failed to instantiate client", exc_info=True)
            return Result(ResultType.FAILED, "Failed to instanciate remote client")

        try:
            client.cluster.list_nodes()
        except Exception as e:
            return Result(ResultType.FAILED, str(e))
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not maas_deployment.is_maas_deployment(deployment):
            return Result(ResultType.FAILED)

        deployment.clusterd_address = url
        deployment.clusterd_certificate_authority = certificate_authority
        deployment.clusterd_certpair = CertPair(
            certificate=certificate, private_key=private_key
        )
        self.deployments_config.write()
        return Result(ResultType.COMPLETED)


class MaasRemoveDeploymentCredentialsStep(BaseStep):
    """Remove deployment credentials."""

    def __init__(
        self,
        deployment_name: str,
        deployments_config: DeploymentsConfig,
    ):
        super().__init__(
            "Remove deployment credentials",
            "Removing deployment credentials",
        )
        self.deployment_name = deployment_name
        self.deployments_config = deployments_config

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not maas_deployment.is_maas_deployment(deployment):
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None) -> Result:
        """Remove credentials from deployment information."""
        deployment = self.deployments_config.get_deployment(self.deployment_name)
        if not maas_deployment.is_maas_deployment(deployment):
            return Result(ResultType.FAILED)
        deployment.clusterd_address = None
        deployment.clusterd_certificate_authority = None
        deployment.clusterd_certpair = None
        # Do not unregister external controllers
        if (
            juju_controller := deployment.juju_controller
        ) and not juju_controller.is_external:
            deployment.juju_account = None
            deployment.juju_controller = None
        self.deployments_config.write()
        return Result(ResultType.COMPLETED)


class MaasAddMachinesToClusterdStep(BaseStep):
    """Add machines from MAAS to Clusterd."""

    nodes: Sequence[tuple[str, str]] | None
    machines: Sequence[dict] | None

    def __init__(self, client: Client, maas_client: maas_client.MaasClient):
        super().__init__("Add machines", "Adding machines to Clusterd")
        self.client = client
        self.maas_client = maas_client
        self.machines = None
        self.nodes = None

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        maas_machines = maas_client.list_machines(self.maas_client)
        LOG.debug(f"Machines fetched: {maas_machines}")
        filtered_machines = []
        for machine in maas_machines:
            if set(machine["roles"]).intersection(
                {
                    maas_deployment.RoleTags.JUJU_CONTROLLER.value,
                    maas_deployment.RoleTags.SUNBEAM.value,
                    maas_deployment.RoleTags.CONTROL.value,
                    maas_deployment.RoleTags.COMPUTE.value,
                    maas_deployment.RoleTags.STORAGE.value,
                }
            ):
                filtered_machines.append(machine)
        LOG.debug(f"Machines containing worker roles: {filtered_machines}")
        if filtered_machines is None or len(filtered_machines) == 0:
            return Result(ResultType.FAILED, "Maas deployment has no machines.")
        clusterd_nodes = self.client.cluster.list_nodes()
        nodes_to_update = []
        for node in clusterd_nodes:
            for machine in filtered_machines:
                if node["name"] == machine["hostname"]:
                    filtered_machines.remove(machine)
                    if sorted(node["role"]) != sorted(machine["roles"]):
                        nodes_to_update.append((machine["hostname"], machine["roles"]))
        self.nodes = nodes_to_update
        self.machines = filtered_machines
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add machines to Juju model."""
        if self.machines is None or self.nodes is None:
            # only happens if is_skip() was not called before, or if run executed
            # even if is_skip reported a failure
            return Result(ResultType.FAILED, "No machines to add / node to update.")
        for machine in self.machines:
            self.client.cluster.add_node_info(
                machine["hostname"], machine["roles"], systemid=machine["system_id"]
            )
        for node in self.nodes:
            self.client.cluster.update_node_info(*node)  # type: ignore
        return Result(ResultType.COMPLETED)


class MaasRemoveMachineFromClusterdStep(BaseStep):
    """Remove machine from Clusterd."""

    def __init__(self, client: Client, node: str):
        super().__init__("Remove machine", "Adding machine from Clusterd")
        self.client = client
        self.node = node

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            self.client.cluster.get_node_info(self.node)
        except NodeNotExistInClusterException:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Add machines to Juju model."""
        try:
            self.client.cluster.remove_node_info(self.node)
        except Exception:
            LOG.debug("Failed to remove machine", exc_info=True)
            return Result(ResultType.FAILED, f"Failed to remove {self.node}")
        return Result(ResultType.COMPLETED)


class MaasDeployMachinesStep(BaseStep):
    """Deploy machines stored in Clusterd in Juju."""

    def __init__(self, client: Client, jhelper: JujuHelper, model: str):
        super().__init__("Deploy machines", "Deploying machines in Juju")
        self.client = client
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        clusterd_nodes = self.client.cluster.list_nodes()
        if len(clusterd_nodes) == 0:
            return Result(ResultType.FAILED, "No machines found in clusterd.")

        juju_machines = self.jhelper.get_machines(self.model)

        nodes_to_deploy = clusterd_nodes.copy()
        nodes_to_update = []
        for node in clusterd_nodes:
            node_machine_id = node["machineid"]
            role = node.get("role")
            if role and (
                maas_deployment.RoleTags.JUJU_CONTROLLER.value in role
                or maas_deployment.RoleTags.SUNBEAM.value in role
            ):
                # Juju Controllers should not be deployed by this step
                nodes_to_deploy.remove(node)
                continue
            for id, machine in juju_machines.items():
                if node["name"] == machine.hostname:
                    if int(id) != node_machine_id and node_machine_id != -1:
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with id {id},"
                            f" expected the id {node['machineid']}.",
                        )
                    if (
                        node["systemid"] != machine.instance_id
                        and node["systemid"] != ""
                    ):
                        return Result(
                            ResultType.FAILED,
                            f"Machine {node['name']} already exists in model"
                            f" {self.model} with systemid {machine.instance_id},"
                            f" expected the systemid {node['systemid']}.",
                        )
                    if node_machine_id == -1:
                        nodes_to_update.append(node)
                    nodes_to_deploy.remove(node)
                    break

        self.nodes_to_deploy = sorted(nodes_to_deploy, key=lambda x: x["name"])
        self.nodes_to_update = nodes_to_update

        if not self.nodes_to_deploy and not self.nodes_to_update:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Deploy machines in Juju."""
        for node in self.nodes_to_deploy:
            self.update_status(status, f"deploying {node['name']}")
            LOG.debug(f"Adding machine {node['name']} to model {self.model}")
            juju_machine = self.jhelper.add_machine(
                "system-id=" + node["systemid"], self.model
            )
            self.client.cluster.update_node_info(
                node["name"], machineid=int(juju_machine)
            )
        self.update_status(status, "waiting for machines to deploy")
        machines = self.jhelper.get_machines(self.model)
        for node in self.nodes_to_update:
            LOG.debug(f"Updating machine {node['name']} in model {self.model}")
            for machine_id, machine in machines.items():
                if machine.hostname == node["name"]:
                    self.client.cluster.update_node_info(
                        node["name"], machineid=int(machine_id)
                    )
                    break
        try:
            self.jhelper.wait_all_machines_deployed(self.model, MACHINE_DEPLOY_TIMEOUT)
        except TimeoutError:
            LOG.debug("Timeout waiting for machines to deploy", exc_info=True)
            return Result(ResultType.FAILED, "Timeout waiting for machines to deploy.")
        return Result(ResultType.COMPLETED)


class MaasDeployInfraMachinesStep(BaseStep):
    """Deploy infra machines."""

    def __init__(
        self, maas_client: maas_client.MaasClient, jhelper: JujuHelper, model: str
    ):
        super().__init__("Deploy Infra machines", "Deploying infra machines")
        self.maas_client = maas_client
        self.jhelper = jhelper
        self.model = model

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        maas_machines = maas_client.list_machines(self.maas_client)
        LOG.debug(f"Machines fetched: {maas_machines}")
        filtered_machines = []
        for machine in maas_machines:
            if set(machine["roles"]).intersection(
                {
                    maas_deployment.RoleTags.SUNBEAM.value,
                }
            ):
                filtered_machines.append(machine)
        LOG.debug(f"Machines containing infra role: {filtered_machines}")
        if filtered_machines is None or len(filtered_machines) == 0:
            return Result(ResultType.FAILED, "Maas deployment has no infra machines.")

        self.machines_to_deploy = filtered_machines.copy()
        juju_machines = self.jhelper.get_machines(self.model)
        LOG.debug(f"Machines already deployed: {juju_machines}")

        for filtered_machine in filtered_machines:
            for id, deployed_machine in juju_machines.items():
                if filtered_machine["hostname"] == deployed_machine.hostname:
                    self.machines_to_deploy.remove(filtered_machine)

        if not self.machines_to_deploy:
            return Result(ResultType.SKIPPED)
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Deploy machines in Juju."""
        for machine in self.machines_to_deploy:
            self.update_status(status, f"deploying {machine['hostname']}")
            LOG.debug(f"Adding machine {machine['hostname']} to model {self.model}")
            self.jhelper.add_machine(
                "system-id=" + machine["system_id"], self.model, JUJU_BASE
            )
        try:
            self.jhelper.wait_all_machines_deployed(self.model, MACHINE_DEPLOY_TIMEOUT)
        except TimeoutError:
            LOG.debug("Timeout waiting for machines to deploy", exc_info=True)
            return Result(ResultType.FAILED, "Timeout waiting for machines to deploy.")
        return Result(ResultType.COMPLETED)


class MaasConfigureMicrocephOSDStep(BaseStep):
    """Configure Microceph OSD disks."""

    def __init__(
        self,
        client: Client,
        maas_client: maas_client.MaasClient,
        jhelper: JujuHelper,
        names: list[str],
        model: str,
    ):
        super().__init__("Configure MicroCeph storage", "Configuring MicroCeph storage")
        self.client = client
        self.maas_client = maas_client
        self.jhelper = jhelper
        self.names = names
        self.model = model
        self.disks_to_configure: dict[str, list[str]] = {}

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(min=2, max=10),
        retry=tenacity.retry_if_exception_type(ActionFailedException),
    )
    def _list_disks(self, unit: str) -> tuple[dict, dict]:
        """Call list-disks action on an unit."""
        LOG.debug("Running list-disks on : %r", unit)
        action_result = self.jhelper.run_action(
            unit, self.model, "list-disks", action_params={"host-only": True}
        )
        LOG.debug(
            "Result after running action list-disks on %r: %r",
            unit,
            action_result,
        )
        osds = ast.literal_eval(action_result.get("osds", "[]"))
        unpartitioned_disks = ast.literal_eval(
            action_result.get("unpartitioned-disks", "[]")
        )
        return osds, unpartitioned_disks

    def _get_microceph_disks(self) -> dict:
        """Retrieve all disks added to microceph.

        Return a dict of format:
            {
                "<machine>": {
                    "osds": ["<disk1_path>", "<disk2_path>"],
                    "unpartitioned_disks": ["<disk3_path>"]
                    "unit": "<unit_name>"
                }
            }
        """
        disks: dict[str, dict] = {}
        default_disk: dict[str, list[str]] = {"osds": [], "unpartitioned_disks": []}
        for name in self.names:
            machine_id = str(self.client.cluster.get_node_info(name)["machineid"])
            unit = self.jhelper.get_unit_from_machine(
                microceph.APPLICATION, machine_id, self.model
            )
            if unit is None:
                raise ValueError(
                    f"{microceph.APPLICATION}'s unit not found on {name}."
                    " Is microceph deployed on this machine?"
                )
            osd_disks, unit_unpartitioned_disks = self._list_disks(unit)
            disks.setdefault(name, copy.deepcopy(default_disk))["osds"].extend(
                osd["path"] for osd in osd_disks
            )
            disks[name]["unpartitioned_disks"].extend(
                uud["path"] for uud in unit_unpartitioned_disks
            )
            disks[name]["unit"] = unit

        return disks

    def _get_maas_disks(self) -> dict:
        """Retrieve all disks from MAAS per machine.

        Return a dict of format:
            {
                "<machine>": ["<disk1_path>", "<disk2_path>"]
            }
        """
        machines = maas_client.list_machines(self.maas_client, hostname=self.names)
        disks = {}
        for machine in machines:
            disks[machine["hostname"]] = [
                device["id_path"]
                for device in machine["storage"][maas_deployment.StorageTags.CEPH.value]
            ]

        return disks

    def _compute_disks_to_configure(
        self, microceph_disks: dict, maas_disks: set[str]
    ) -> list[str]:
        """Compute the disks that need to be configured for the machine."""
        machine_osds = set(microceph_disks["osds"])
        machine_unpartitioned_disks = set(microceph_disks["unpartitioned_disks"])
        machine_unit = microceph_disks["unit"]
        if len(maas_disks) == 0:
            raise ValueError(
                f"Machine {machine_unit!r} does not have any"
                f" {maas_deployment.StorageTags.CEPH.value!r} disk defined."
            )
        # Get all disks that are in Ceph but not in MAAS
        unknown_osds = machine_osds - maas_disks
        # Get all disks that are in MAAS but neither in Ceph nor unpartitioned
        missing_disks = maas_disks - machine_osds - machine_unpartitioned_disks
        # Disks to partition
        disks_to_configure = maas_disks.intersection(machine_unpartitioned_disks)

        if len(unknown_osds) > 0:
            raise ValueError(
                f"Machine {machine_unit!r} has OSDs from disks unknown to MAAS:"
                f" {unknown_osds}"
            )
        if len(missing_disks) > 0:
            raise ValueError(
                f"Machine {machine_unit!r} is missing disks: {missing_disks}"
            )
        if len(disks_to_configure) > 0:
            LOG.debug(
                "Unit %r will configure the following disks: %r",
                machine_unit,
                disks_to_configure,
            )
            return list(disks_to_configure)

        return []

    def is_skip(self, status: Status | None = None) -> Result:
        """Determines if the step should be skipped or not."""
        try:
            microceph_disks = self._get_microceph_disks()
            LOG.debug("Computing disk mapping: %r", microceph_disks)
        except ValueError as e:
            LOG.debug("Failed to list microceph disks from units", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        try:
            maas_disks = self._get_maas_disks()
            LOG.debug("Maas disks: %r", maas_disks)
        except ValueError as e:
            LOG.debug("Failed to list disks from MAAS", exc_info=True)
            return Result(ResultType.FAILED, str(e))

        disks_to_configure: dict[str, list[str]] = {}

        for name in self.names:
            try:
                machine_disks_to_configure = self._compute_disks_to_configure(
                    microceph_disks[name], set(maas_disks.get(name, []))
                )
            except ValueError as e:
                LOG.debug(
                    "Failed to compute disks to configure for machine %r",
                    name,
                    exc_info=True,
                )
                return Result(ResultType.FAILED, str(e))
            if len(machine_disks_to_configure) > 0:
                disks_to_configure[microceph_disks[name]["unit"]] = (
                    machine_disks_to_configure
                )

        if len(disks_to_configure) == 0:
            LOG.debug("No disks to configure, skipping step.")
            return Result(ResultType.SKIPPED)

        self.disks_to_configure = disks_to_configure
        return Result(ResultType.COMPLETED)

    def run(self, status: Status | None = None) -> Result:
        """Configure local disks on microceph."""
        for unit, disks in self.disks_to_configure.items():
            try:
                LOG.debug("Running action add-osd on %r", unit)
                action_result = self.jhelper.run_action(
                    unit,
                    self.model,
                    "add-osd",
                    action_params={
                        "device-id": ",".join(disks),
                    },
                )
                LOG.debug(
                    "Result after running action add-osd on %r: %r", unit, action_result
                )
            except (UnitNotFoundException, ActionFailedException) as e:
                LOG.debug("Failed to run action add-osd on %r", unit, exc_info=True)
                return Result(ResultType.FAILED, str(e))
        return Result(ResultType.COMPLETED)


def _to_joined_range(subnet_ranges: dict[str, list[dict]], label: str) -> str:
    """Convert a list of ip ranges to a string for cni config.

    Current cni config format is: <ip start>-<ip end>,<ip start>-<ip end>,...
    """
    lb_range = []
    for ip_ranges in subnet_ranges.values():
        for ip_range in ip_ranges:
            if ip_range["label"] == label:
                lb_range.append(f"{ip_range['start']}-{ip_range['end']}")
    if len(lb_range) == 0:
        raise ValueError("No ip range found for label: " + label)
    return ",".join(lb_range)


def get_space_api_range(
    client: maas_client.MaasClient,
    space: str,
    api_label: str,
) -> str:
    """Get the API range for a specific space."""
    try:
        ranges = maas_client.get_ip_ranges_from_space(client, space)
        LOG.debug("Public ip ranges: %r", ranges)
    except ValueError as e:
        raise ValueError(f"Failed to find ip ranges for space: {space!r}") from e
    try:
        parsed_ranges = _to_joined_range(ranges, api_label)
        LOG.debug("Parsed range: %r", parsed_ranges)
    except ValueError as e:
        raise ValueError(
            f"Failed to find public ip range for label: {api_label!r}",
        ) from e
    return parsed_ranges


class MaasDeployK8SApplicationStep(k8s.DeployK8SApplicationStep):
    """Deploy K8S application using Terraform."""

    deployment: maas_deployment.MaasDeployment
    ranges: str | None

    def __init__(
        self,
        deployment: maas_deployment.MaasDeployment,
        client: Client,
        maas_client: maas_client.MaasClient,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        model: str,
        accept_defaults: bool = False,
    ):
        super().__init__(
            deployment,
            client,
            tfhelper,
            jhelper,
            manifest,
            model,
            accept_defaults,
        )
        self.maas_client = maas_client

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
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

    def _get_loadbalancer_range(self) -> str | None:
        """Return loadbalancer range from public space."""
        return self.ranges

    def is_skip(self, status: Status | None = None):
        """Determines if the step should be skipped or not."""
        internal_space = self.deployment.get_space(Networks.INTERNAL)
        public_space = self.deployment.get_space(Networks.PUBLIC)
        try:
            _ = get_space_api_range(
                self.maas_client, public_space, self.deployment.public_api_label
            )
        except ValueError as e:
            LOG.debug(
                "Failed to get API range for public space: %r",
                public_space,
                exc_info=True,
            )
            return Result(ResultType.FAILED, str(e))
        try:
            internal_range = get_space_api_range(
                self.maas_client, internal_space, self.deployment.internal_api_label
            )
        except ValueError as e:
            LOG.debug(
                "Failed to get API range for internal space: %r",
                internal_space,
                exc_info=True,
            )
            return Result(ResultType.FAILED, str(e))
        self.ranges = internal_range

        return super().is_skip(status)


class MaasSetHypervisorUnitsOptionsStep(SetHypervisorUnitsOptionsStep):
    """Configure hypervisor settings on the machines."""

    def __init__(
        self,
        client: Client,
        maas_client: maas_client.MaasClient,
        names: list[str],
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
    ):
        super().__init__(
            client,
            names,
            jhelper,
            model,
            manifest,
            "Apply hypervisor settings",
            "Applying hypervisor settings",
        )
        self.maas_client = maas_client

    def _get_maas_nics(self) -> dict[str, str | None]:
        """Retrieve fist nic from MAAS per machine with compute tag.

        Return a dict of format:
            {
                "<machine>": "<nic1_name>" | None
            }
        """
        machines = maas_client.list_machines(self.maas_client, hostname=self.names)
        nics = {}
        for machine in machines:
            machine_nics = [
                nic["name"]
                for nic in machine["nics"]
                if maas_deployment.NicTags.COMPUTE.value in nic["tags"]
            ]

            if len(machine_nics) > 0:
                # take first nic with compute tag
                nic = machine_nics[0]
            else:
                nic = None
            nics[machine["hostname"]] = nic

        return nics

    def is_skip(self, status: Status | None = None):
        """Determines if the step should be skipped or not."""
        result = super().is_skip(status)
        if result.result_type == ResultType.FAILED:
            return result
        nics = self._get_maas_nics()
        LOG.debug("Nics: %r", nics)

        for machine, nic in nics.items():
            if nic is None:
                nic_tag = maas_deployment.NicTags.COMPUTE.value
                return Result(
                    ResultType.FAILED,
                    f"Machine {machine} does not have any {nic_tag} nic defined.",
                )

        self.nics = nics
        return Result(ResultType.COMPLETED)


class MaasUserQuestions(BaseStep):
    """Ask user configuration questions."""

    def __init__(
        self,
        client: Client,
        maas_client: maas_client.MaasClient,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(
            "Collect cloud configuration", "Collecting cloud configuration"
        )
        self.client = client
        self.maas_client = maas_client
        self.accept_defaults = accept_defaults
        self.manifest = manifest

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user."""
        return True

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Prompt the user for basic cloud configuration.

        Prompts the user for required information for cloud configuration.

        :param console: the console to prompt on
        :type console: rich.console.Console (Optional)
        """
        self.variables = sunbeam.core.questions.load_answers(
            self.client, CLOUD_CONFIG_SECTION
        )
        for section in ["user", "external_network"]:
            if not self.variables.get(section):
                self.variables[section] = {}
        preseed = {}
        if self.manifest and (user := self.manifest.core.config.user):
            preseed = user.model_dump(by_alias=True)
        user_bank = sunbeam.core.questions.QuestionBank(
            questions=maas_deployment.maas_user_questions(self.maas_client),
            console=console,
            preseed=preseed,
            previous_answers=self.variables.get("user"),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        self.variables["user"]["remote_access_location"] = sunbeam_utils.REMOTE_ACCESS
        # External Network Configuration
        preseed = {}
        if self.manifest and (
            ext_network := self.manifest.core.config.external_network
        ):
            preseed = ext_network.model_dump(by_alias=True)
        ext_net_bank = sunbeam.core.questions.QuestionBank(
            questions=ext_net_questions(),
            console=console,
            preseed=preseed,
            previous_answers=self.variables.get("external_network"),
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        self.variables["external_network"]["cidr"] = ext_net_bank.cidr.ask()
        external_network = ipaddress.ip_network(
            self.variables["external_network"]["cidr"]
        )
        external_network_hosts = list(external_network.hosts())
        default_gateway = self.variables["external_network"].get("gateway") or str(
            external_network_hosts[0]
        )
        self.variables["external_network"]["gateway"] = ext_net_bank.gateway.ask(
            new_default=default_gateway
        )

        default_allocation_range = (
            self.variables["external_network"].get("range")
            or f"{external_network_hosts[1]}-{external_network_hosts[-1]}"
        )
        self.variables["external_network"]["range"] = ext_net_bank.range.ask(
            new_default=default_allocation_range
        )

        self.variables["external_network"]["physical_network"] = VARIABLE_DEFAULTS[
            "external_network"
        ]["physical_network"]

        self.variables["external_network"]["network_type"] = (
            ext_net_bank.network_type.ask()
        )
        if self.variables["external_network"]["network_type"] == "vlan":
            self.variables["external_network"]["segmentation_id"] = (
                ext_net_bank.segmentation_id.ask()
            )
        else:
            self.variables["external_network"]["segmentation_id"] = 0

        self.variables["user"]["run_demo_setup"] = user_bank.run_demo_setup.ask()
        if self.variables["user"]["run_demo_setup"]:
            # User configuration
            self.variables["user"]["username"] = user_bank.username.ask()
            self.variables["user"]["password"] = user_bank.password.ask()
            self.variables["user"]["cidr"] = user_bank.cidr.ask()
            nameservers = user_bank.nameservers.ask()
            self.variables["user"]["dns_nameservers"] = (
                nameservers.split() if nameservers else []
            )
            self.variables["user"]["security_group_rules"] = (
                user_bank.security_group_rules.ask()
            )

        sunbeam.core.questions.write_answers(
            self.client, CLOUD_CONFIG_SECTION, self.variables
        )

    def run(self, status: Status | None = None) -> Result:
        """Run the step to completion."""
        return Result(ResultType.COMPLETED)


class MaasClusterStatusStep(ClusterStatusStep):
    deployment: maas_deployment.MaasDeployment

    def models(self) -> list[str]:
        """List of models to query status from."""
        models = [self.deployment.infra_model]
        if self.jhelper.model_exists(self.deployment.openstack_machines_model):
            models.append(self.deployment.openstack_machines_model)
            return models

        LOG.debug(
            f"Model {self.deployment.openstack_machines_model} not found. This is "
            "expected when cluster is bootstrapped but not deployed yet. "
            "Skipping model."
        )
        return models

    def _update_microcluster_status(self, status: dict, microcluster_status: dict):
        """How to update microcluster status in the status dict.

        In MAAS, clusterd is deployed as an application. We have to map the unit
        to the machine to display the correct status.
        """
        for member, member_status in microcluster_status.items():
            for node_status in status[self.deployment.infra_model].values():
                clusterd_status = node_status.get("applications", {}).get(
                    clusterd.APPLICATION
                )
                if not clusterd_status:
                    # machine does not have a cluster unit
                    continue
                unit_name = clusterd_status.get("name")
                if member == unit_name.replace("/", "-"):
                    node_status["clusterd-status"] = member_status
                    break


class MaasCreateLoadBalancerIPPoolsStep(CreateLoadBalancerIPPoolsStep):
    """Create Loadbalancer IP Pool."""

    def __init__(
        self,
        deployment: maas_deployment.MaasDeployment,
        client: Client,
        maas_client: maas_client.MaasClient,
    ):
        super().__init__(client)
        self.deployment = deployment
        self.maas_client = maas_client

    def _to_range(self, subnet_ranges: dict[str, list[dict]], label: str) -> list[str]:
        """Convert a list of ip ranges to a list for cni config.

        Current cni config format is: [<ip start>-<ip end>,<ip  start>-<ip end>,...]
        """
        lb_range = []
        for ip_ranges in subnet_ranges.values():
            for ip_range in ip_ranges:
                if ip_range["label"] == label:
                    lb_range.append(f"{ip_range['start']}-{ip_range['end']}")

        return lb_range

    def ippools(self) -> dict[str, list[str]]:
        """IPAddress pools.

        Pools should be in format of
        {<pool name>: <List of ipaddresses>}
        """
        public_space = self.deployment.get_space(Networks.PUBLIC)

        try:
            public_ranges = maas_client.get_ip_ranges_from_space(
                self.maas_client, public_space
            )
            LOG.debug("Public ip ranges: %r", public_ranges)
        except ValueError:
            LOG.debug("Failed to ip ranges for space: %r", public_space, exc_info=True)
            return {}

        public_metallb_range = self._to_range(  # noqa
            public_ranges, self.deployment.public_api_label
        )

        return {self.deployment.public_ip_pool: public_metallb_range}


class MaasConfigSRIOVStep(BaseStep):
    """Prompt user for SR-IOV configuration."""

    def __init__(
        self,
        deployment: maas_deployment.MaasDeployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__("SR-IOV Settings", "Configure SR-IOV")
        self.client = client
        self.maas_client = maas_client.MaasClient.from_deployment(deployment)
        self.jhelper = jhelper
        self.model = model
        self.manifest = manifest
        self.accept_defaults = accept_defaults
        self.variables: dict = {}

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
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

    def _get_compute_machines(self) -> list:
        return maas_client.list_machines(
            self.maas_client, tags=maas_deployment.RoleTags.COMPUTE.value
        )

    def _get_sriov_nics_from_maas_tags(self, machine: dict) -> dict:
        # Retrieve nics that contain one of the following tags:
        # * sriov:physnet1
        # * sriov:no-physnet
        # * sriov-hw-offload:physnet1
        # * sriov-hw-offload:no-physnet
        #
        # At the moment, the snap automatically configures the Neutron
        # sr-iov agent for nics that do not support hardware offloading.
        # As such, we don't really need to make the distinction between
        # sriov and sriov-hw-offload nics. However, this function will
        # accept "sriov-hw-offload" in case we decide to handle those
        # tagged nics differently.
        sriov_nics = {}
        pattern = r"^sriov(?:-hw-offload)?:([\w_-]+)$"
        for nic in machine["nics"]:
            for tag in nic["tags"]:
                groups = re.findall(pattern, tag)
                if not groups or len(groups) > 1:
                    continue
                physnet = groups[0]
                if physnet in ("none", "null", "no-physnet"):
                    physnet = None
                sriov_nics[nic["name"]] = {
                    "name": nic["name"],
                    "physnet": physnet,
                    "mac_address": nic["mac_address"],
                }
        return sriov_nics

    def _get_pci_config(
        self, compute_machines: list[dict]
    ) -> Tuple[list[dict], dict[str, list]]:
        pci_whitelist: list[dict] = []
        excluded_devices: dict[str, list] = {}

        if self.manifest:
            pci_config = self.manifest.core.config.pci
            if pci_config and pci_config.device_specs:
                pci_whitelist = copy.deepcopy(pci_config.device_specs)
                LOG.debug("PCI whitelist from manifest: %s", pci_whitelist)
            if pci_config and pci_config.excluded_devices:
                excluded_devices = copy.deepcopy(pci_config.excluded_devices)
                LOG.debug("PCI exclude list from manifest: %s", excluded_devices)

        for machine in compute_machines:
            sriov_tagged_nics = self._get_sriov_nics_from_maas_tags(machine)

            node_name = machine["hostname"]
            # nics reported by the compute-hypervisor snap.
            snap_nics = nic_utils.fetch_nics(
                self.client, node_name, self.jhelper, self.model
            )

            for snap_nic in snap_nics["nics"]:
                nic_name = snap_nic["name"]
                if not (snap_nic["product_id"] and snap_nic["vendor_id"]):
                    LOG.debug("Ignoring nic, not a PCI device: %s", snap_nic["name"])
                    continue

                if nic_name in sriov_tagged_nics:
                    # whitelisted through maas tag
                    nic_utils.whitelist_sriov_nic(
                        node_name,
                        snap_nic,
                        pci_whitelist,
                        excluded_devices,
                        sriov_tagged_nics[nic_name]["physnet"],
                    )
                else:
                    # Add to the per-node exclusion list.
                    nic_utils.exclude_sriov_nic(node_name, snap_nic, excluded_devices)

        return pci_whitelist, excluded_devices

    def run(self, status: Status | None = None) -> Result:
        """Apply individual hypervisor settings."""
        self.update_status(status, "setting PCI configuration")

        compute_machines = self._get_compute_machines()

        pci_whitelist, excluded_devices = self._get_pci_config(compute_machines)
        self.variables["pci_whitelist"] = pci_whitelist
        self.variables["excluded_devices"] = excluded_devices
        sunbeam.core.questions.write_answers(
            self.client, PCI_CONFIG_SECTION, self.variables
        )

        for machine in compute_machines:
            node_name = machine["hostname"]
            node_excluded_devices = excluded_devices.get(node_name) or []
            LOG.debug("PCI excluded devices [%s]: %s", node_name, node_excluded_devices)

            app = "openstack-hypervisor"
            action_cmd = "set-hypervisor-local-settings"

            node = self.client.cluster.get_node_info(node_name)
            machine_id = str(node.get("machineid"))
            unit = self.jhelper.get_unit_from_machine(app, machine_id, self.model)
            try:
                self.jhelper.run_action(
                    unit,
                    self.model,
                    action_cmd,
                    action_params={
                        "pci-excluded-devices": json.dumps(node_excluded_devices),
                    },
                )
            except (ActionFailedException, TimeoutError):
                msg = f"Unable to set hypervisor {node_name} configuration"
                LOG.error(msg, exc_info=True)
                return Result(ResultType.FAILED, msg)

        return Result(ResultType.COMPLETED)


class MaasEndpointsConfigurationStep(EndpointsConfigurationStep):
    """Configuration endpoints for local provider."""

    def __init__(
        self,
        deployment: maas_deployment.MaasDeployment,
        client: Client,
        m_client: maas_client.MaasClient,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(client, manifest, accept_defaults)
        self.deployment = deployment
        self.maas_client = m_client
        self.internal_range = None
        self.public_range = None

    # TODO(gboutry): Typing unhappy while it should be.
    @typing.no_type_check
    def _internal_loadbalancer_range(self) -> list[str]:
        """Load the load balancer range."""
        if not self.internal_range:
            self.internal_range = get_space_api_range(
                self.maas_client,
                self.deployment.get_space(Networks.INTERNAL),
                self.deployment.internal_api_label,
            ).split(",")
        return self.internal_range

    @typing.no_type_check
    def _public_loadbalancer_range(self) -> list[str]:
        """Load the public load balancer range."""
        if not self.public_range:
            self.public_range = get_space_api_range(
                self.maas_client,
                self.deployment.get_space(Networks.PUBLIC),
                self.deployment.public_api_label,
            ).split(",")
        return self.public_range

    def _validate_endpoint(self, endpoint: str, ip: str) -> bool:
        """Let's validate the endpoint."""
        ip_address = ipaddress.ip_address(ip)
        if endpoint in ("public", "rgw"):
            ip_ranges = self._public_loadbalancer_range()
        else:
            ip_ranges = self._internal_loadbalancer_range()

        for ip_range in ip_ranges:
            start_ip, end_ip = parse_ip_range(ip_range)
            # Check if all IP versions match
            if (
                ip_address.version != start_ip.version
                or start_ip.version != end_ip.version
            ):
                LOG.debug(
                    "IP version mismatch in range: ip=%s (v%d) vs range=%s-%s "
                    "(v%d-v%d)",
                    ip_address,
                    ip_address.version,
                    start_ip,
                    end_ip,
                    start_ip.version,
                    end_ip.version,
                )
                continue

            is_in_range = start_ip <= ip_address <= end_ip  # type: ignore
            LOG.debug(
                "IP %s %s in loadbalancer range %s-%s",
                ip_address,
                "is" if is_in_range else "is not",
                start_ip,
                end_ip,
            )
            if is_in_range:
                return True
        return False


class MaasConfigDPDKStep(BaseConfigDPDKStep):
    """Prompt user for SR-IOV configuration."""

    def __init__(
        self,
        deployment: maas_deployment.MaasDeployment,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        manifest: Manifest | None = None,
        accept_defaults: bool = False,
    ):
        super().__init__(client, jhelper, model, manifest, accept_defaults)
        self.maas_client = maas_client.MaasClient.from_deployment(deployment)

    def _get_compute_machines(self) -> list:
        return maas_client.list_machines(
            self.maas_client, tags=maas_deployment.RoleTags.COMPUTE.value
        )

    def _get_dpdk_nics_from_maas_tags(self, machine: dict) -> list[str]:
        # Retrieve nics that contain the "neutron:dpdk" tag.

        dpdk_nics = []
        for nic in machine["nics"]:
            if "neutron:dpdk" in nic["tags"]:
                dpdk_nics.append(nic["name"])
        return dpdk_nics

    def _get_dpdk_port_map(self, compute_machines: list[dict]) -> dict[str, list[str]]:
        # The list of DPDK interfaces for each compute node.
        dpdk_port_map = collections.defaultdict(list)

        # Get DPDK nics defined using MAAS tags.
        for machine in compute_machines:
            node_name = machine["hostname"]
            dpdk_port_map[node_name] = self._get_dpdk_nics_from_maas_tags(machine)

        # Get DPDK nics defined in the manifest.
        dpdk_manifest_ports = self._get_dpdk_manifest_ports() or {}
        # Merge the port lists.
        for node_name, ports in dpdk_manifest_ports.items():
            for port in ports:
                if port not in dpdk_port_map[node_name]:
                    dpdk_port_map[node_name].append(port)

        return dict(dpdk_port_map)

    def _prompt_nics(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        # In MAAS mode, we'll fetch the nics from the manifest and
        # MASS tags, without actually showing user prompts.
        compute_machines = self._get_compute_machines()
        self.nics = self._get_dpdk_port_map(compute_machines)

    def run(self, status: Status | None = None) -> Result:
        """Apply individual hypervisor settings."""
        self.update_status(status, "setting DPDK ports")

        compute_machines = self._get_compute_machines()
        for machine in compute_machines:
            node_name = machine["hostname"]
            dpdk_ports = self.nics.get(node_name) or []
            LOG.debug("DPDK ports [%s]: %s", node_name, dpdk_ports)

            app = "openstack-hypervisor"
            action_cmd = "set-hypervisor-local-settings"

            node = self.client.cluster.get_node_info(node_name)
            machine_id = str(node.get("machineid"))
            unit = self.jhelper.get_unit_from_machine(app, machine_id, self.model)
            try:
                self.jhelper.run_action(
                    unit,
                    self.model,
                    action_cmd,
                    action_params={
                        "ovs-dpdk-ports": ",".join(dpdk_ports or ""),
                    },
                )
            except (ActionFailedException, TimeoutError):
                msg = f"Unable to set hypervisor {node_name} configuration"
                LOG.error(msg, exc_info=True)
                return Result(ResultType.FAILED, msg)

        return Result(ResultType.COMPLETED)
