# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Manage Neutron dynamic routing agents on MicroOVN network nodes."""

import hashlib
import ipaddress
import json
import re
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal, NotRequired, TypedDict

import click
from openstack.exceptions import HttpException, SDKException
from packaging.version import Version
from pydantic import (
    Field,
    IPvAnyAddress,
    IPvAnyInterface,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from rich.console import Console
from sunbeam_ndr_contract import CONTRACT_VERSION, parse_instances
from tenacity import Retrying, retry_if_exception, stop_after_delay, wait_fixed

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import TerraformPlanLockConflictException
from sunbeam.core.checks import JujuLoginCheck, run_preflight_checks
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import ActionFailedException, JujuHelper
from sunbeam.core.manifest import FeatureConfig, ManifestModel
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.openstack_api import get_admin_connection
from sunbeam.core.ovn import microovn_application_name_for_node
from sunbeam.features.interface.v1.base import EnableDisableFeature, FeatureError
from sunbeam.features.interface.v1.openstack import OPENSTACK_TERRAFORM_VARS
from sunbeam.features.observability.feature import (
    refresh_machine_collector_integrations,
)
from sunbeam.provider.maas.client import MaasClient, is_maas_deployment, list_machines
from sunbeam.steps.microovn import AGENT_APP, CONFIG_KEY
from sunbeam.utils import pass_method_obj

ACTION = "set-dynamic-routing-instances"
VALIDATE_ACTION = "validate-dynamic-routing-instances"
AGENT_TYPE = "BGP dynamic routing agent"
NEUTRON_OPTION = "enable-dynamic-routing"
PLACEMENT_LOCK = "dynamic-routing-placement"
FORCE_HELP = "Stop agents even if BGP speakers are still bound. Retain their bindings."
ROUTER_ID_POOL = ipaddress.IPv4Network("198.18.0.0/15")
console = Console()

DomainName = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=50)
]
NodeName = Annotated[str, StringConstraints(min_length=1)]
InterfaceName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$", min_length=1, max_length=15)
]
AddressFamily = Literal["ipv4", "ipv6"]
Instance = TypedDict(
    "Instance",
    {
        "domain": str,
        "family": AddressFamily,
        "service-name": str,
        "host": str,
        "router-id": NotRequired[str],
        "peering": NotRequired[dict[str, dict[str, str | int]]],
    },
)
Instances = dict[str, list[Instance]]
MULTICAST_V4 = ipaddress.IPv4Network("224.0.0.0/4")


class ProviderTrunkNode(ManifestModel):
    """Static peering address on a managed OVS port."""

    address: IPvAnyInterface

    @field_validator("address", mode="before")
    @classmethod
    def require_prefix_length(cls, value: object) -> object:
        """Require the operator to set a prefix length explicitly."""
        if not isinstance(value, str) or "/" not in value:
            raise ValueError("provider-trunk address must include a prefix length")
        return value


class DedicatedUplinkNode(ManifestModel):
    """An existing peering interface and optional source address."""

    interface: InterfaceName | None = None
    address: IPvAnyAddress | None = None


class ProviderTrunk(ManifestModel):
    """A provider VLAN with a static peering address per node."""

    physnet: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+$", min_length=1)
    ]
    vlan: int = Field(ge=1, le=4094, strict=True)
    nodes: dict[NodeName, ProviderTrunkNode] = Field(min_length=1)


class DedicatedUplink(ManifestModel):
    """An existing peering attachment on each selected node."""

    nodes: dict[NodeName, DedicatedUplinkNode] = Field(min_length=1)


class Peering(ManifestModel):
    """One attachment type for the routing domain."""

    provider_trunk: ProviderTrunk | None = Field(None, alias="provider-trunk")
    dedicated_uplink: DedicatedUplink | None = Field(None, alias="dedicated-uplink")

    @model_validator(mode="after")
    def validate_attachment(self) -> "Peering":
        """Require one, and only one, attachment."""
        if (self.provider_trunk is None) == (self.dedicated_uplink is None):
            raise ValueError("peering requires exactly one attachment")
        return self


class RoutingDomain(ManifestModel):
    """Placement settings for an operator-managed routing domain."""

    address_families: list[AddressFamily] = Field(
        default=["ipv4"], alias="address-families", min_length=1, max_length=1
    )
    peering: Peering

    @model_validator(mode="after")
    def validate_address_family(self) -> "RoutingDomain":
        """Validate declared addresses against the domain family."""
        attachment = self.peering.provider_trunk or self.peering.dedicated_uplink
        if attachment:
            for node, settings in attachment.nodes.items():
                address = settings.address
                if address is None:
                    continue
                ip = (
                    address.ip
                    if isinstance(
                        address, (ipaddress.IPv4Interface, ipaddress.IPv6Interface)
                    )
                    else address
                )
                if f"ipv{ip.version}" != self.address_families[0]:
                    raise ValueError(
                        f"{node}: peering address must match the domain family"
                    )
                if (
                    ip.is_unspecified
                    or ip.is_multicast
                    or ip.is_loopback
                    or "%" in str(address)
                ):
                    raise ValueError(
                        f"{node}: peering address must be an unscoped unicast IP"
                    )
        return self


class DynamicRoutingConfig(FeatureConfig):
    """Desired routing domains. An omitted map reuses the applied configuration."""

    routing_domains: dict[DomainName, RoutingDomain] | None = Field(
        None, alias="routing-domains"
    )
    router_id_pool: ipaddress.IPv4Network | None = Field(None, alias="router-id-pool")


def resolve_domains(
    config: DynamicRoutingConfig, state: dict
) -> dict[str, RoutingDomain]:
    """Use an explicit manifest or load the previously saved placement."""
    domains = config.routing_domains
    if domains is None:
        try:
            domains = DynamicRoutingConfig.model_validate(
                {"routing-domains": state.get("routing-domains", {})}
            ).routing_domains
        except ValidationError as error:
            raise FeatureError(
                "The saved dynamic routing placement is invalid. "
                "Drain legacy domains with an explicit routing-domains map "
                "before adding them back with peering attachments."
            ) from error
    if not domains and not state:
        raise FeatureError("Declare at least one routing domain in the manifest.")
    return domains or {}


def agent_instance(domain: str, family: AddressFamily, node: str) -> Instance:
    """Generate stable per-family agent settings on a network node."""
    version = f"v{family[-1]}"
    service = f"agent-{domain}-{version}"
    host = f"{node.lower()}.{domain}.{version}"
    if len(host) > 255 or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise FeatureError(f"Invalid NDR agent host {host!r}.")

    return {
        "domain": domain,
        "family": family,
        "service-name": service,
        "host": host,
    }


def resolve_instances(
    domains: dict[str, RoutingDomain],
    nodes: list[dict],
    maas_interfaces: dict[str, list[dict]] | None = None,
) -> Instances:
    """Expand domains using registered Sunbeam network node names."""
    inventory = {node["name"]: node for node in nodes}
    network_nodes = {
        node["name"]
        for node in nodes
        if "network" in node.get("role", [])
        and node.get("machineid") not in (None, -1, "-1", "")
    }
    instances: Instances = {}
    identities: set[str] = set()
    trunk_sources: set[tuple[str, int, str]] = set()

    for domain, config in sorted(domains.items()):
        trunk = config.peering.provider_trunk
        uplink = config.peering.dedicated_uplink
        placement = trunk.nodes if trunk else uplink.nodes if uplink else {}
        eligible = ", ".join(sorted(network_nodes)) or "none"
        family = config.address_families[0]
        for node in sorted(placement):
            if node not in network_nodes:
                if node not in inventory:
                    reason = "is not registered in this deployment"
                elif "network" not in inventory[node].get("role", []):
                    reason = "does not have the network role"
                else:
                    reason = "does not have a deployed Juju machine"
                raise FeatureError(
                    f"Routing domain {domain!r}: {node!r} {reason}. "
                    f"Eligible node names: {eligible}."
                )
            instance = agent_instance(domain, family, node)
            host = instance["host"]
            if host in identities:
                raise FeatureError(f"Duplicate NDR agent host {host!r}.")
            identities.add(host)
            if trunk:
                address = trunk.nodes[node].address
                source = (trunk.physnet, trunk.vlan, str(address.ip))
                if source in trunk_sources:
                    raise FeatureError(
                        f"Routing domain {domain!r} on {node!r}: duplicate peering "
                        f"address {address.ip} on {trunk.physnet} VLAN {trunk.vlan}."
                    )
                trunk_sources.add(source)
                instance["peering"] = {
                    "provider-trunk": {
                        "physnet": trunk.physnet,
                        "vlan": trunk.vlan,
                        "address": str(address),
                    }
                }
            elif uplink:
                settings = uplink.nodes[node]
                interface = settings.interface
                if maas_interfaces is not None:
                    tag = f"ndr:{domain}"
                    matches = [
                        nic["name"]
                        for nic in maas_interfaces.get(node, [])
                        if tag in nic["tags"]
                    ]
                    if len(matches) != 1 or (interface and interface != matches[0]):
                        raise FeatureError(
                            f"Routing domain {domain!r} on {node!r} requires exactly "
                            f"one MAAS interface matching {tag!r} and the declared "
                            "name if present."
                        )
                    interface = matches[0]
                if not interface:
                    raise FeatureError(
                        f"Routing domain {domain!r} on {node!r} requires an explicit "
                        "dedicated-uplink interface in Manual mode."
                    )
                peering: dict[str, str | int] = {"interface": interface}
                if settings.address is not None:
                    peering["address"] = str(settings.address)
                instance["peering"] = {"dedicated-uplink": peering}
            instances.setdefault(node, []).append(instance)

    return instances


def reserve_router_ids(
    requested_pool: ipaddress.IPv4Network | None,
    state: dict,
    current: Instances,
    desired: Instances,
) -> dict:
    """Reuse persisted IDs and reserve free IDs before starting agents."""
    try:
        saved_pool = ipaddress.IPv4Network(state.get("router-id-pool", ROUTER_ID_POOL))
    except (TypeError, ValueError) as error:
        raise FeatureError("The saved NDR router ID pool is invalid.") from error
    pool = requested_pool if requested_pool is not None else saved_pool
    if pool != saved_pool and state.get("enabled") == "true":
        raise FeatureError(
            "Disable dynamic routing before changing the router ID pool."
        )
    if pool.num_addresses < 4 or pool.overlaps(MULTICAST_V4):
        raise FeatureError("The NDR router ID pool must contain usable IPv4 addresses.")

    # Seed older placements with their existing IDs and keep all reservations
    # in the current pool, including for drained agents that can return.
    saved = state.get("router-ids", {}) if pool == saved_pool else {}
    if not isinstance(saved, dict):
        raise FeatureError("The saved NDR router ID allocations are invalid.")
    reservations = dict(saved)
    for placements in current.values():
        for instance in placements:
            host, saved_address = instance["host"], instance.get("router-id")
            if saved_address is None:
                raise FeatureError(f"Missing saved NDR router ID for {host!r}.")
            if reservations.setdefault(host, saved_address) != saved_address:
                raise FeatureError(
                    f"Conflicting saved router IDs for NDR agent {host!r}."
                )
    used: set[ipaddress.IPv4Address] = set()
    first, last = pool.network_address, pool.broadcast_address
    for host, value in reservations.items():
        try:
            address = ipaddress.IPv4Address(value)
        except (TypeError, ValueError) as error:
            raise FeatureError(f"Invalid saved NDR router ID for {host!r}.") from error
        if not isinstance(value, str) or not first < address < last or address in used:
            raise FeatureError(
                f"Invalid or conflicting saved NDR router ID for {host!r}."
            )
        used.add(address)

    for placements in desired.values():
        for instance in placements:
            host = instance["host"]
            if host not in reservations:
                address = next_router_id(host, pool, used)
                reservations[host] = str(address)
                used.add(address)
            instance["router-id"] = reservations[host]
    return {"router-id-pool": str(pool), "router-ids": reservations}


def next_router_id(
    host: str, pool: ipaddress.IPv4Network, used: set[ipaddress.IPv4Address]
) -> ipaddress.IPv4Address:
    """Find the first free router ID from the host's stable hash position."""
    count = pool.num_addresses - 2
    digest = hashlib.sha256(host.encode()).digest()
    candidate = int.from_bytes(digest[:4], "big") % count
    for step in range(min(count, len(used) + 1)):
        address = pool.network_address + 1 + (candidate + step) % count
        if address not in used:
            return address
    raise FeatureError(f"The NDR router ID pool {pool} is exhausted.")


def check_family_changes(domains: dict[str, RoutingDomain], current: Instances) -> None:
    """Require old agent placements to be removed before changing their family."""
    for instances in current.values():
        for instance in instances:
            name = instance["domain"]
            domain = domains.get(name)
            if domain and instance["family"] != domain.address_families[0]:
                raise FeatureError(
                    f"Routing domain {name!r} still has {instance['family']} agents. "
                    "To drain it, remove the domain from the manifest and apply it "
                    "before adding the domain with the new address family."
                )


def check_peering_changes(
    current: Instances, desired: Instances, *, resolved: bool = False
) -> None:
    """Require a drain before changing a running unit's peering attachment."""
    desired_attachments = {
        instance["domain"]: domain_attachment(instance)
        for instances in desired.values()
        for instance in instances
    }
    for node, instances in current.items():
        for instance in instances:
            domain = instance["domain"]
            if (
                domain in desired_attachments
                and domain_attachment(instance) != desired_attachments[domain]
            ):
                raise FeatureError(
                    f"NDR agent {instance['host']!r} has a different or legacy "
                    "peering attachment. Drain its domain before replacing it."
                )
        previous = {instance["host"]: instance.get("peering") for instance in instances}
        for instance in desired.get(node, []):
            host = instance["host"]
            if host not in previous:
                continue
            current_peering = previous[host]
            desired_peering = instance["peering"]
            uplink = desired_peering.get("dedicated-uplink")
            if not resolved and uplink is not None and "address" not in uplink:
                current_uplink = (current_peering or {}).get("dedicated-uplink", {})
                current_peering = {
                    "dedicated-uplink": {
                        key: value
                        for key, value in current_uplink.items()
                        if key != "address"
                    }
                }
            if current_peering != desired_peering:
                raise FeatureError(
                    f"NDR agent {host!r} has a different or legacy peering attachment. "
                    "Drain it before applying the new attachment."
                )


def domain_attachment(instance: Instance) -> tuple[str | int, ...]:
    """Compare settings shared across all nodes of one routing domain."""
    peering = instance.get("peering", {})
    if trunk := peering.get("provider-trunk"):
        return ("provider-trunk", trunk["physnet"], trunk["vlan"])
    if "dedicated-uplink" in peering:
        return ("dedicated-uplink",)
    return ()


def instance_action_params(instances: list[Instance]) -> dict[str, int | str]:
    """Validate and render a complete per-unit Juju placement."""
    try:
        parse_instances(instances)
    except (TypeError, ValueError) as error:
        raise FeatureError(f"Invalid NDR placement: {error}") from error
    return {"contract-version": CONTRACT_VERSION, "instances": json.dumps(instances)}


def validate_unit_placements(
    jhelper: JujuHelper, model: str, units: dict[str, str], desired: Instances
) -> None:
    """Run read-only host validation and accept only expected normalization."""
    for node, unit in units.items():
        instances = desired.get(node, [])
        try:
            result = jhelper.run_action(
                unit, model, VALIDATE_ACTION, instance_action_params(instances)
            )
        except (ActionFailedException, FeatureError) as error:
            raise FeatureError(
                f"NDR peering preflight failed on {unit}: {error}"
            ) from error
        try:
            normalized = json.loads(result["instances"])
            if (
                str(result["contract-version"]) != str(CONTRACT_VERSION)
                or not isinstance(normalized, list)
                or len(normalized) != len(instances)
            ):
                raise ValueError
            parse_instances(normalized)
            by_host = {instance["host"]: instance for instance in normalized}
            for instance in instances:
                candidate = by_host[instance["host"]]
                expected = instance
                uplink = instance["peering"].get("dedicated-uplink")
                if uplink is not None and "address" not in uplink:
                    selected = candidate["peering"]["dedicated-uplink"].get("address")
                    if not isinstance(selected, str) or "%" in selected:
                        raise ValueError
                    ip = ipaddress.ip_address(selected)
                    expected = {
                        **instance,
                        "peering": {"dedicated-uplink": {**uplink, "address": str(ip)}},
                    }
                if candidate != expected:
                    raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise FeatureError(
                f"{unit} returned an invalid NDR preflight result."
            ) from error
        if instances:
            desired[node] = normalized


def check_microovn(jhelper: JujuHelper) -> None:
    """Require Neutron's deployed OVN control plane to use the MicroOVN proxy."""
    neutron = jhelper.get_model_status(OPENSTACK_MODEL).apps.get("neutron")
    ovsdb_relations = neutron.relations.get("ovsdb-cms", []) if neutron else []
    if not any(rel.related_app == "sunbeam-ovn-proxy" for rel in ovsdb_relations):
        raise FeatureError(
            "Dynamic routing requires MicroOVN. "
            "Neutron must be related to sunbeam-ovn-proxy through ovsdb-cms."
        )


def resolve_units(
    jhelper: JujuHelper,
    model: str,
    nodes: list[dict],
    desired: Instances,
    current: Instances,
) -> dict[str, str]:
    """Resolve every affected node before applying any changes."""
    inventory = {node["name"]: node for node in nodes}
    status = jhelper.get_model_status(model)
    units: dict[str, str] = {}
    checked_apps: set[str] = set()
    for name in sorted(desired.keys() | current.keys()):
        node = inventory.get(name)
        if node is None:
            raise FeatureError(f"Previously configured NDR node {name!r} is missing.")
        principal = microovn_application_name_for_node(node)
        agent_app = AGENT_APP + principal.removeprefix("microovn")
        app_status = status.apps.get(principal)
        machine_id = str(node.get("machineid"))
        matches = [
            subordinate
            for unit in (app_status.units.values() if app_status else [])
            if unit.machine == machine_id
            for subordinate in (unit.subordinates or {})
            if subordinate.startswith(f"{agent_app}/")
        ]
        if len(matches) != 1 or matches[0] in units.values():
            raise FeatureError(
                f"Node {name!r} must have exactly one distinct {agent_app} "
                "subordinate on its MicroOVN unit."
            )
        if agent_app not in checked_apps:
            actions = jhelper.get_application_actions(agent_app, model)
            for required in (ACTION, VALIDATE_ACTION):
                if required not in actions:
                    raise FeatureError(
                        f"{agent_app} does not provide the {required} action."
                    )
            checked_apps.add(agent_app)
        units[name] = matches[0]
    return units


class DynamicRoutingFeature(EnableDisableFeature[DynamicRoutingConfig]):
    """Configure the Neutron dynamic routing workload."""

    name = "dynamic-routing"
    version = Version("0.0.1")

    def config_type(self) -> type[DynamicRoutingConfig]:
        """Return the manifest configuration model."""
        return DynamicRoutingConfig

    @click.command()
    @click.option("--force", is_flag=True, help=FORCE_HELP)
    @pass_method_obj
    def enable_cmd(self, deployment: Deployment, force: bool) -> None:
        """Enable dynamic routing or reconcile an updated placement."""
        context = click.get_current_context()
        manifest_path = (
            context.parent.params.get("manifest") if context.parent else None
        )
        manifest = deployment.get_manifest(manifest_path)
        feature = manifest.get_feature(self.name)
        config = feature.config if feature else None
        if not isinstance(config, DynamicRoutingConfig):
            config = DynamicRoutingConfig.model_validate({})
        self.apply_placement(deployment, config, force=force)

    @click.command()
    @click.option("--force", is_flag=True, help=FORCE_HELP)
    @pass_method_obj
    def disable_cmd(self, deployment: Deployment, force: bool) -> None:
        """Stop the agents and disable the Neutron plugin."""
        self.apply_placement(
            deployment,
            DynamicRoutingConfig.model_validate({}),
            force=force,
            enabled=False,
        )

    def run_enable_plans(
        self, deployment: Deployment, config: DynamicRoutingConfig, show_hints: bool
    ) -> None:
        """Apply the requested placement."""
        self.apply_placement(deployment, config)

    def run_disable_plans(self, deployment: Deployment, show_hints: bool) -> None:
        """Stop the agents and disable the server plugin."""
        self.apply_placement(
            deployment, DynamicRoutingConfig.model_validate({}), enabled=False
        )

    def agent_bindings(
        self, deployment: Deployment, keystone_helper: JujuHelper, hosts: set[str]
    ) -> list[str]:
        """Get current speaker bindings without modifying any Neutron object."""
        if not hosts:
            return []
        # A charm config change can briefly make the API unavailable while
        # Juju still reports Neutron active.
        retry = Retrying(
            retry=retry_if_exception(
                lambda error: (
                    isinstance(error, HttpException) and error.status_code == 503
                )
            ),
            stop=stop_after_delay(120),
            wait=wait_fixed(5),
            reraise=True,
        )
        try:
            with get_admin_connection(
                keystone_helper, deployment, region_name=deployment.get_region_name()
            ) as connection:
                network = connection.network
                return retry(
                    lambda: [
                        f"Agent {agent.host} ({agent.id}) hosts speaker "
                        f"{speaker.name} ({speaker.id})."
                        for agent in network.agents(agent_type=AGENT_TYPE)
                        if agent.host in hosts
                        for speaker in network.get_bgp_speakers_hosted_by_dragent(agent)
                    ]
                )
        except SDKException as error:
            raise FeatureError("Unable to check Neutron speaker bindings.") from error

    def apply_placement(
        self,
        deployment: Deployment,
        config: DynamicRoutingConfig,
        *,
        force: bool = False,
        enabled: bool = True,
    ) -> None:
        """Serialize placement changes across the deployment's admin nodes."""
        client = deployment.get_client()
        with self._placement_lock(client):
            self._apply_placement(deployment, client, config, force, enabled)

    @contextmanager
    def node_removal_guard(self, client: Client, node: str) -> Iterator[None]:
        """Require an empty NDR placement and prevent re-placement during removal."""
        with self._placement_lock(client):
            if self.get_feature_info(client).get("instances", {}).get(node):
                raise click.ClickException(
                    f"Drain dynamic routing placement for {node!r} before removing it. "
                    "Remove the node from the routing domains in the deployment "
                    "manifest and reapply dynamic routing while its unit is reachable."
                )
            yield

    @contextmanager
    def _placement_lock(self, client: Client) -> Iterator[None]:
        """Serialize changes to NDR placement and the nodes it can use."""
        lock = {
            "ID": str(uuid.uuid4()),
            "Operation": "Dynamic routing placement",
            "Who": socket.gethostname(),
            "Created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f001Z"),
        }
        try:
            client.cluster.lock_terraform_plan(PLACEMENT_LOCK, lock)
        except TerraformPlanLockConflictException as error:
            raise FeatureError(
                "Another dynamic routing placement change is in progress."
            ) from error
        try:
            yield
        finally:
            client.cluster.unlock_terraform_plan(PLACEMENT_LOCK, lock)

    def _apply_placement(
        self,
        deployment: Deployment,
        client: Client,
        config: DynamicRoutingConfig,
        force: bool,
        enabled: bool,
    ) -> None:
        """Validate and apply a full placement under the deployment lock."""
        state = self.get_feature_info(client)
        if not enabled and state.get("enabled") != "true":
            return
        domains = resolve_domains(config, state) if enabled else {}
        current: Instances = state.get("instances", {})
        check_family_changes(domains, current)
        run_preflight_checks([JujuLoginCheck(deployment.juju_account)], console)
        jhelper = JujuHelper(deployment.juju_controller)
        keystone_helper = deployment.get_juju_helper(keystone=True)
        if enabled:
            check_microovn(jhelper)
        nodes = client.cluster.list_nodes()
        maas_interfaces = None
        uplink_nodes = {
            node
            for domain in domains.values()
            if (uplink := domain.peering.dedicated_uplink) is not None
            for node in uplink.nodes
        }
        if uplink_nodes and is_maas_deployment(deployment):
            maas = MaasClient.from_deployment(deployment)
            maas_interfaces = {
                machine["hostname"]: machine["nics"]
                for machine in list_machines(maas, hostname=sorted(uplink_nodes))
            }
        desired = resolve_instances(domains, nodes, maas_interfaces)
        allocations = reserve_router_ids(config.router_id_pool, state, current, desired)
        check_peering_changes(current, desired)
        model = deployment.openstack_machines_model
        units = resolve_units(jhelper, model, nodes, desired, current)
        validate_unit_placements(jhelper, model, units, desired)
        check_peering_changes(current, desired, resolved=True)
        desired_apps = {units[node].split("/")[0] for node in desired}
        current_apps = {units[node].split("/")[0] for node in current}

        current_hosts = {
            instance["host"] for items in current.values() for instance in items
        }
        desired_hosts = {
            instance["host"] for items in desired.values() for instance in items
        }
        bindings = self.agent_bindings(
            deployment, keystone_helper, current_hosts - desired_hosts
        )
        for binding in bindings:
            click.echo(f"{binding} Removing it may withdraw routes.", err=True)
        if bindings and not force:
            raise FeatureError("Unbind the affected agents or use --force.")
        self.check_neutron_config(deployment, jhelper)

        offer = None
        if enabled and (current_apps or desired_apps):
            openstack_plan = deployment.get_tfhelper("openstack-plan")
            openstack_plan.init()
            offer = openstack_plan.output().get("rabbitmq-offer-url")
            if not offer:
                raise FeatureError("The control plane has no RabbitMQ offer for NDR.")

        routing_domains = state.get("routing-domains", {})
        if enabled:
            routing_domains = {
                domain: settings.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                for domain, settings in domains.items()
            }
            self.update_feature_info(
                client,
                {"enabled": "true", "routing-domains": routing_domains, **allocations},
            )
            self.configure_neutron(deployment, jhelper, enabled=True)
            jhelper.wait_until_desired_status(
                OPENSTACK_MODEL, ["neutron"], status=["active"]
            )
            self.check_neutron_api(deployment, keystone_helper)
            for binding in self.agent_bindings(
                deployment, keystone_helper, desired_hosts - current_hosts
            ):
                click.echo(
                    f"{binding} The retained binding may resume advertising.", err=True
                )
            self.configure_amqp(deployment, current_apps | desired_apps, offer)
            self.wait_for_amqp(jhelper, model, units, desired)

        self.apply_units(client, jhelper, model, units, current, desired)

        if enabled and desired_hosts:
            self.wait_for_agents(deployment, keystone_helper, desired_hosts)
        elif not enabled:
            self.configure_neutron(deployment, jhelper, enabled=False)
        if not enabled or current_apps - desired_apps:
            self.configure_amqp(deployment, desired_apps, offer)
        self.update_feature_info(
            client,
            {
                "enabled": str(enabled).lower(),
                "instances": desired,
                "routing-domains": routing_domains,
                **allocations,
            },
        )
        if enabled and desired_apps:
            refresh_machine_collector_integrations(deployment, jhelper)
        click.echo(f"Dynamic routing {'configured' if enabled else 'disabled'}.")

    def check_neutron_config(self, deployment: Deployment, jhelper: JujuHelper) -> None:
        """Ensure the installed charm supports the feature-owned option."""
        neutron = deployment.get_manifest().core.software.charms.get("neutron-k8s")
        if neutron and neutron.config and NEUTRON_OPTION in neutron.config:
            raise FeatureError(
                f"Manage {NEUTRON_OPTION} through the dynamic-routing feature, "
                "and remove the charm override from the manifest."
            )
        if NEUTRON_OPTION not in jhelper.get_app_config("neutron", OPENSTACK_MODEL):
            raise FeatureError(f"The neutron charm does not provide {NEUTRON_OPTION}.")

    def check_neutron_api(
        self, deployment: Deployment, keystone_helper: JujuHelper
    ) -> None:
        """Check the active Neutron API before configuring DR agents."""
        try:
            with get_admin_connection(
                keystone_helper, deployment, region_name=deployment.get_region_name()
            ) as connection:
                # Juju can still report active while Neutron reloads its workers.
                def check_bgp_api() -> None:
                    aliases = {
                        extension.alias for extension in connection.network.extensions()
                    }
                    missing = {"bgp", "bgp_dragent_scheduler"} - aliases
                    if missing:
                        raise FeatureError(
                            f"Neutron API is missing NDR extensions: "
                            f"{', '.join(sorted(missing))}."
                        )
                    # The extensions list does not exercise the BGP speaker tables.
                    next(connection.network.bgp_speakers(limit=1), None)

                retry = Retrying(
                    retry=retry_if_exception(
                        lambda error: (
                            isinstance(error, FeatureError)
                            or (
                                isinstance(error, HttpException)
                                and error.status_code == 503
                            )
                        )
                    ),
                    stop=stop_after_delay(120),
                    wait=wait_fixed(5),
                    reraise=True,
                )
                retry(check_bgp_api)
        except HttpException as error:
            raise FeatureError(
                f"Neutron BGP API check failed (HTTP {error.status_code})."
            ) from error
        except SDKException as error:
            raise FeatureError("Unable to check the Neutron BGP API.") from error

    def wait_for_amqp(
        self, jhelper: JujuHelper, model: str, units: dict[str, str], desired: Instances
    ) -> None:
        """Wait for RabbitMQ relation credentials on units that will run agents."""
        pending = {node: units[node] for node in desired}
        retry = Retrying(
            retry=retry_if_exception(lambda error: isinstance(error, FeatureError)),
            stop=stop_after_delay(120),
            wait=wait_fixed(5),
            reraise=True,
        )
        for attempt in retry:
            with attempt:
                for node, unit in list(pending.items()):
                    try:
                        result = jhelper.run_action(
                            unit,
                            model,
                            VALIDATE_ACTION,
                            instance_action_params(desired[node]),
                            timeout=30,
                        )
                    except ActionFailedException as error:
                        raise FeatureError(
                            "Unable to check NDR RabbitMQ credentials on "
                            f"{unit}: {error}"
                        ) from error
                    if str(result.get("amqp-ready", False)).lower() == "true":
                        del pending[node]
                if pending:
                    raise FeatureError(
                        "Waiting for NDR RabbitMQ credentials on "
                        f"{', '.join(sorted(pending.values()))}."
                    )

    def apply_units(
        self,
        client: Client,
        jhelper: JujuHelper,
        model: str,
        units: dict[str, str],
        current: Instances,
        desired: Instances,
    ) -> None:
        """Apply each validated unit plan and persist recoverable progress."""
        for node, unit in units.items():
            instances = desired.get(node, [])
            params = instance_action_params(instances)
            # Persist both sets so retry or disable can recover a lost action response.
            previous = current.get(node, [])
            before = {item["host"]: item for item in previous}
            before.update({item["host"]: item for item in instances})
            current[node] = list(before.values())
            self.update_feature_info(client, {"enabled": "true", "instances": current})
            try:
                result = jhelper.run_action(unit, model, ACTION, params)
            except ActionFailedException as error:
                try:
                    status = jhelper.run_action(unit, model, VALIDATE_ACTION, params)
                    saved = json.loads(status["saved-instances"])
                except (ActionFailedException, KeyError, TypeError, ValueError):
                    saved = None
                if saved == previous:
                    if previous:
                        current[node] = previous
                    else:
                        del current[node]
                    self.update_feature_info(client, {"instances": current})
                    raise
                if saved == instances:
                    raise FeatureError(
                        f"{unit} saved its placement but did not confirm readiness. "
                        "Retry the placement operation."
                    ) from error
                raise
            if instances and str(result.get("configured", False)).lower() != "true":
                raise FeatureError(
                    f"{unit} saved its placement but did not confirm readiness. "
                    "Check the peering interfaces and addresses and the AMQP "
                    "relation, then retry."
                )
            if instances:
                current[node] = instances
            else:
                del current[node]
            self.update_feature_info(client, {"instances": current})

    def configure_neutron(
        self, deployment: Deployment, jhelper: JujuHelper, *, enabled: bool
    ) -> None:
        """Set NDR on the installed charm and persist it for future Terraform runs."""
        config = {NEUTRON_OPTION: enabled}
        jhelper.set_app_config("neutron", OPENSTACK_MODEL, config)
        helper = deployment.get_tfhelper("openstack-plan")
        helper.update_tfvars(
            deployment.get_client(),
            deployment.get_manifest(),
            tfvar_config=OPENSTACK_TERRAFORM_VARS,
            override_tfvars={"neutron-config": config},
        )

    def configure_amqp(
        self, deployment: Deployment, applications: set[str], offer: str | None
    ) -> None:
        """Apply the feature-owned RabbitMQ integrations."""
        helper = deployment.get_tfhelper("microovn-plan")
        helper.init()
        helper.update_tfvars_and_apply_tf(
            deployment.get_client(),
            deployment.get_manifest(),
            tfvar_config=CONFIG_KEY,
            override_tfvars={
                "ndr_rabbitmq_offer_url": offer if applications else None,
                "ndr_applications": sorted(applications),
            },
            tf_apply_extra_args=["-target=juju_integration.amqp"],
        )

    def wait_for_agents(
        self, deployment: Deployment, keystone_helper: JujuHelper, expected: set[str]
    ) -> None:
        """Wait for a new heartbeat from each desired agent after placement."""
        try:
            with get_admin_connection(
                keystone_helper, deployment, region_name=deployment.get_region_name()
            ) as connection:
                retry = Retrying(
                    stop=stop_after_delay(180),
                    wait=wait_fixed(5),
                    reraise=True,
                )
                baseline: dict[str, str] | None = None
                for attempt in retry:
                    with attempt:
                        present = {
                            agent.host: agent.last_heartbeat_at
                            for agent in connection.network.agents(
                                agent_type=AGENT_TYPE
                            )
                            if agent.host in expected and agent.is_alive
                        }
                        if baseline is None:
                            baseline = present
                        fresh = {
                            host
                            for host, heartbeat in present.items()
                            if heartbeat and heartbeat != baseline.get(host)
                        }
                        if missing := expected - fresh:
                            raise FeatureError(
                                "Waiting for new NDR agent heartbeats: "
                                f"{', '.join(sorted(missing))}."
                            )
        except SDKException as error:
            raise FeatureError("Unable to check NDR agent heartbeats.") from error
