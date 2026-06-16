# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""MAAS management."""

import collections
import logging
from typing import TYPE_CHECKING, Sequence, overload

from rich.console import Console

from sunbeam.core.deployment import Deployment, Networks
from sunbeam.core.deployments import DeploymentsConfig
from sunbeam.lazy import LazyImport
from sunbeam.provider.maas.deployment import (
    MaasDeployment,
    RoleTags,
    StorageTags,
    is_maas_deployment,
)

if TYPE_CHECKING:
    import maas.client as maas_client  # type: ignore [import-untyped]
    import maas.client.bones as maas_bones  # type: ignore [import-untyped]
else:
    maas_client = LazyImport("maas.client")
    maas_bones = LazyImport("maas.client.bones")

LOG = logging.getLogger(__name__)
console = Console()

# "amd64" is the Debian/MAAS name for x86_64.
DEFAULT_ARCHITECTURE = "amd64"
DPU_IMAGE_TAG_PREFIX = "dpu-image-"


def parse_image_name_from_tags(tag_names: list[str] | None) -> str | None:
    """Return the MAAS boot resource name from a dpu-image-<name> machine tag.

    :param tag_names: MAAS tag names assigned to the machine.
    :raises ValueError: if more than one dpu-image tag is present, or if a
        dpu-image tag has an empty image name (e.g. ``dpu-image-``).
    """
    if not tag_names:
        return None
    matches = [
        tag[len(DPU_IMAGE_TAG_PREFIX) :]
        for tag in tag_names
        if tag.startswith(DPU_IMAGE_TAG_PREFIX)
    ]
    if len(matches) > 1:
        raise ValueError(
            "Multiple dpu-image tags found on machine: "
            + ", ".join(matches)
            + ". Only one dpu-image-<name> tag is allowed."
        )
    if len(matches) == 1 and not matches[0]:
        raise ValueError(
            "Invalid dpu-image tag: image name is empty. "
            "Use dpu-image-<name> with a non-empty name."
        )
    return matches[0] if matches else None


class MaasClient:
    """Facade to MAAS APIs."""

    def __init__(self, url: str, token: str, resource_tag: str | None = None):
        self._client = maas_client.connect(url, apikey=token)
        self.resource_tag = resource_tag

    def list_boot_resource_names(self) -> set[str]:
        """Return the names of all boot resources registered in MAAS."""
        try:
            resources = self._client.boot_resources.list.__self__._handler.read()  # type: ignore # noqa
        except maas_bones.CallError as e:
            raise ValueError(f"Failed to list MAAS boot resources: {e}") from e
        return {resource["name"] for resource in resources}

    def ensure_boot_resource_name_exists(self, name: str) -> None:
        """Verify that a MAAS boot resource name exists.

        :param name: Boot resource name from a dpu-image-<name> tag.
        :raises ValueError: if the image is not registered in MAAS.
        """
        boot_resources = self.list_boot_resource_names()
        if name in boot_resources:
            return
        raise ValueError(f"Image with name {name} is missing from maas boot-resources")

    def ensure_tag(self, tag: str):
        """Create a tag if it does not already exist."""
        try:
            self._client.tags.create(name=tag)  # type: ignore
        except maas_bones.CallError as e:
            if "already exists" not in str(e):
                raise e

    def list_machines(self, **kwargs) -> list[dict]:
        """List machines."""
        if self.resource_tag:
            tags = kwargs.pop("tags", None)
            if tags:
                tags += "," + self.resource_tag
            else:
                tags = self.resource_tag
            kwargs["tags"] = tags
        try:
            return self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        except maas_bones.CallError as e:
            if "No such tag(s)" in str(e):
                raise ValueError(str(e))
            raise e

    def get_machine(self, machine: str) -> dict:
        """Get machine."""
        kwargs = {
            "hostname": machine,
        }
        if self.resource_tag:
            kwargs["tags"] = self.resource_tag
        machines = self._client.machines.list.__self__._handler.read(**kwargs)  # type: ignore # noqa
        if len(machines) == 0:
            raise ValueError(f"Machine {machine!r} not found.")
        if len(machines) > 1:
            raise ValueError(f"Machine {machine!r} not unique.")
        return machines[0]

    def get_machine_volume_groups(self, machine_id: str) -> list[dict]:
        """Get machine volume groups."""
        machine = self._client.machines.get(machine_id)  # type: ignore
        return machine.volume_groups._handler.read(system_id=machine.system_id)

    def list_spaces(self) -> list[dict]:
        """List spaces."""
        return self._client.spaces.list.__self__._handler.read()  # type: ignore

    def get_space(self, space: str) -> dict:
        """Get a specific space."""
        for space_raw in self.list_spaces():
            if space_raw["name"] == space:
                return space_raw
        else:
            raise ValueError(f"Space {space!r} not found.")

    def get_subnets(self, space: str | None = None) -> list[dict]:
        """List subnets."""
        if space:
            # check if space exists
            _ = self.get_space(space)
        subnets_response: list = self._client.subnets.list()  # type: ignore
        subnets = []
        for subnet in subnets_response:
            if space is None or subnet.space == space:
                subnets.append(subnet._data)
        return subnets

    def get_ip_ranges(self, subnet: dict) -> list[dict]:
        """List ip ranges.

        Only list reserved types as it is the only one we are interested in.
        """
        ip_ranges_response: list = self._client.ip_ranges.list()  # type: ignore

        subnet_id = subnet["id"]
        ip_ranges = []
        for ip_range in ip_ranges_response:
            if ip_range.subnet.id == subnet_id and ip_range.type.value == "reserved":
                ip_ranges.append(ip_range._data)
        return ip_ranges

    def get_dns_servers(self) -> list[str]:
        """Get configured upstream dns."""
        return self._client.maas.get_upstream_dns()  # type: ignore

    def get_http_proxy(self) -> str | None:
        """Get configured http proxy."""
        return self._client.maas.get_http_proxy()  # type: ignore

    @classmethod
    def from_deployment(cls, deployment: Deployment) -> "MaasClient":
        """Return client connected to active deployment."""
        if not is_maas_deployment(deployment):
            raise ValueError("Deployment is not a MAAS deployment.")
        return cls(
            deployment.url,
            deployment.token,
            deployment.resource_tag,
        )


def _to_root_disk(
    physical_devices: list[dict],
    virtual_device: dict | None,
    partition: dict | None = None,
) -> dict:
    """Convert device to root disk."""
    if partition:
        size = partition["size"]
    elif virtual_device:
        size = virtual_device["size"]
    elif len(physical_devices) == 1:
        size = physical_devices[0]["size"]
    else:
        raise ValueError(
            "Expected exactly one physical device when"
            " no partition/virtual blockdevice found."
        )
    root_disk = {
        "physical_blockdevices": [
            {
                "name": device["name"],
                "tags": device["tags"],
                "size": device["size"],
            }
            for device in physical_devices
        ],
        "virtual_blockdevice": (
            {
                "name": virtual_device["name"],
                "size": virtual_device["size"],
                "tags": virtual_device["tags"],
            }
            if virtual_device
            else None
        ),
        "root_partition": {
            "size": size,
        },
    }
    return root_disk


def _find_root_devices(client, machine: dict) -> dict | None:  # noqa: C901
    """Find device(s) hosting the root partition.

    Iterate over blockdevices and partitions to find the root partition.
    From there, either the partition is on a physical device or a virtual device.
    If it is a physical device, return the device.
    If it is a virtual device, check if it is an LVM, try to find underlying physical
    devices.
    """
    root_blockdevice = None
    root_partition = None
    blockdevices = machine["blockdevice_set"]

    for blockdevice in blockdevices:
        if fs := blockdevice.get("filesystem"):
            if fs.get("label") == "root" or fs.get("mount_point") == "/":
                root_blockdevice = blockdevice
                break

        for partition in blockdevice.get("partitions", []):
            if fs := partition.get("filesystem"):
                if fs.get("label") == "root" or fs.get("mount_point") == "/":
                    root_blockdevice = blockdevice
                    root_partition = partition
                    break

    if root_blockdevice is None:
        LOG.debug("No root blockdevice found, neither physical nor virtual")
        return None

    if root_blockdevice["type"] == "physical":
        LOG.debug("Root device is a physical device")
        return _to_root_disk([root_blockdevice], None, root_partition)

    underlying_devices: list[dict] = []

    if root_blockdevice["type"] != "virtual":
        LOG.debug("Unknown block device type: %r", root_blockdevice)
        return None

    volume_groups = client.get_machine_volume_groups(machine["system_id"])

    for vg in volume_groups:
        for lv in vg["logical_volumes"]:
            if lv["id"] == root_blockdevice["id"]:
                LOG.debug("Root device is a logical volume")
                underlying_devices.extend(
                    {
                        "type": device["type"],
                        "id": device["id"],
                        # physical blockdevices don't have a device_id
                        "device_id": device.get("device_id"),
                    }
                    for device in vg["devices"]
                )
    LOG.debug("underlying_devices: %r", underlying_devices)
    physical_devices = []
    for device in underlying_devices:
        if device["type"] == "physical":
            device_id = device.get("id")
        else:
            device_id = device.get("device_id")
        if device_id is None:
            LOG.debug("Unknown device_id for device: %r", device)
            continue
        for blockdevice in machine["physicalblockdevice_set"]:
            if blockdevice["id"] == device_id:
                physical_devices.append(blockdevice)
    return _to_root_disk(physical_devices, root_blockdevice, root_partition)


def _convert_raw_machine(machine_raw: dict, root_disk: dict | None) -> dict:
    storage_tags = StorageTags.values()
    storage_devices: dict[str, list[dict]] = {tag: [] for tag in storage_tags}
    for blockdevice in machine_raw["blockdevice_set"]:
        for tag in blockdevice["tags"]:
            if tag in storage_tags:
                storage_devices[tag].append(
                    {
                        "name": blockdevice["name"],
                        "id_path": blockdevice["id_path"],
                    }
                )

    spaces = []
    nics = []
    for interface in machine_raw["interface_set"]:
        if (vlan := interface.get("vlan")) and (space := vlan.get("space")):
            spaces.append(space)
        nics.append(
            {
                "id": interface["id"],
                "name": interface["name"],
                "mac_address": interface["mac_address"],
                "tags": interface["tags"],
            }
        )

    # MAAS returns architecture as e.g. "amd64/generic" or "arm64/generic".
    # Normalise to the bare arch string ("amd64" / "arm64").
    raw_arch = machine_raw.get("architecture", "")
    architecture = raw_arch.split("/")[0] if raw_arch else DEFAULT_ARCHITECTURE

    is_dpu = bool(machine_raw.get("is_dpu", False))
    tag_names = machine_raw.get("tag_names") or []
    image_name = parse_image_name_from_tags(tag_names)

    machine = {
        "system_id": machine_raw["system_id"],
        "hostname": machine_raw["hostname"],
        "roles": list(set(tag_names).intersection(RoleTags.values())),
        "zone": machine_raw["zone"]["name"],
        "status": machine_raw["status_name"],
        "root_disk": root_disk,
        "storage": storage_devices,
        "spaces": list(set(spaces)),
        "nics": nics,
        "cores": machine_raw["cpu_count"],
        "memory": machine_raw["memory"],
        "architecture": architecture,
        "is_dpu": is_dpu,
    }
    if image_name:
        machine["image_name"] = image_name
    return machine


def list_machines(client: MaasClient, **extra_args) -> list[dict]:
    """List machines in deployment, return consumable list of dicts."""
    machines_raw = client.list_machines(**extra_args)

    machines = []
    for machine in machines_raw:
        machines.append(
            _convert_raw_machine(machine, _find_root_devices(client, machine))
        )
    return machines


def get_machine(client: MaasClient, machine: str) -> dict:
    """Get machine in deployment, return consumable dict."""
    machine_raw = client.get_machine(machine)
    machine_dict = _convert_raw_machine(
        machine_raw, _find_root_devices(client, machine_raw)
    )
    LOG.debug("Retrieved machine %s: %r", machine, machine_dict)
    return machine_dict


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


def map_spaces(
    deployments_config: DeploymentsConfig,
    deployment: MaasDeployment,
    client: MaasClient,
    mapping: dict[Networks, str],
):
    """Map space to network."""
    fetched_spaces = {}
    for network, space in mapping.items():
        if space not in fetched_spaces:
            fetched_spaces[space] = client.get_space(space)
        space_raw = fetched_spaces[space]
        deployment.network_mapping[network.value] = space_raw["name"]
    deployments_config.update_deployment(deployment)
    deployments_config.write()


def unmap_spaces(
    deployments_config: DeploymentsConfig,
    deployment: MaasDeployment,
    networks: Sequence[Networks],
):
    """Unmap networks."""
    for network in networks:
        deployment.network_mapping.pop(network.value, None)
    deployments_config.update_deployment(deployment)
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


def _convert_raw_ip_range(ip_range_raw: dict) -> dict:
    """Convert raw ip range to consumable dict."""
    return {
        "label": ip_range_raw["comment"],
        "start": ip_range_raw["start_ip"],
        "end": ip_range_raw["end_ip"],
    }


def get_ip_ranges_from_space(client: MaasClient, space: str) -> dict[str, list[dict]]:
    """Return all IP ranges from a space.

    Return a dict with the CIDR as key and a list of IP ranges as value.
    """
    subnets = client.get_subnets(space)
    ip_ranges = {}
    for subnet in subnets:
        ranges_raw = client.get_ip_ranges(subnet)
        ranges = []
        for ip_range in ranges_raw:
            ranges.append(_convert_raw_ip_range(ip_range))
        if len(ranges) > 0:
            ip_ranges[subnet["cidr"]] = ranges
    return ip_ranges


def get_ifname_from_space(client: MaasClient, space: str, **extra_args) -> str | None:
    """Get interface name for the given space.

    The machines are filtered by options in **kwargs and the first machine is used
    to get the corresponding interface for the space.
    """
    machines_raw = client.list_machines(**extra_args)
    if not machines_raw:
        return None

    machine = machines_raw[0]
    for interface in machine.get("interface_set", {}):
        for link in interface.get("links", {}):
            if link.get("subnet", {}).get("vlan", {}).get("space") == space:
                return interface.get("name")

    return None
