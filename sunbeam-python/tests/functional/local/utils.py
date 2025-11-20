# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import io
import json
import logging
import os
import subprocess
import typing
import uuid

import yaml

from sunbeam import utils as sunbeam_utils

from . import snap

# Sunbeam may also be installed through tox but we're actually interested in
# using the snap executable.
SUNBEAM_BINARY = "/snap/bin/sunbeam"
SUNBEAM_GROUP = "snap_daemon"

TEST_DEMO_CLOUD_NAME = "sunbeam-test-demo"
TEST_ADMIN_CLOUD_NAME = "sunbeam-test-admin"


def sunbeam_command(cmd: str, capture_output=False) -> int | str | None:
    """Run the specified Sunbeam command.

    Use this helper after calling "sunbeam prepare-node-script"
    to run as "snap_daemon" and have the necessary privileges.

    Consider disabling output capturing if the output is not needed
    by the caller. This way, the test log will contain the command output.
    """
    sg_cmd = ["sg", SUNBEAM_GROUP, f"{SUNBEAM_BINARY} {cmd}"]
    if capture_output:
        return subprocess.check_output(sg_cmd, text=True)
    else:
        return subprocess.check_call(sg_cmd, text=True)


def get_sunbeam_deployments() -> dict:
    cmd = [SUNBEAM_BINARY, "deployment", "list", "--format", "yaml"]
    raw_yaml = subprocess.check_output(cmd, text=True)
    return yaml.safe_load(io.StringIO(raw_yaml))


def install_bootstrap_prerequisites():
    logging.info("Installing Sunbeam bootstrap prerequisites.")
    script = subprocess.check_output(
        [SUNBEAM_BINARY, "prepare-node-script", "--bootstrap"]
    )
    subprocess.check_output(["bash", "-x"], input=script)


def bootstrap_local_cluster(
    manifest_path: str | None = None,
    roles: list[str] | None = None,
):
    install_bootstrap_prerequisites()

    if not roles:
        roles = ["control", "compute", "storage"]
    role_arg = ",".join(roles)

    logging.info("Bootstrapping Sunbeam. Manifest: %s, roles: %s", manifest_path, roles)
    cmd = f"-v cluster bootstrap --accept-defaults --role {role_arg}"
    if manifest_path:
        cmd += f" --manifest {manifest_path}"
    sunbeam_command(cmd)


def ensure_openstack_snap_installed(snap_channel: str | None = None):
    logging.info("Ensuring that the Openstack snap in installed.")
    snap_cache = snap.SnapCache()
    openstack_snap = snap_cache["openstack"]
    if not openstack_snap.present:
        openstack_snap.ensure(snap.SnapState.Present, channel=snap_channel)


def ensure_local_cluster_bootstrapped(
    manifest_path: str | None = None,
    openstack_snap_channel: str | None = None,
    roles: list[str] | None = None,
):
    ensure_openstack_snap_installed(openstack_snap_channel)

    info = get_sunbeam_deployments() or {}
    deployments = info.get("deployments") or []
    active_deployment_name = info.get("active")

    if not deployments:
        logging.info("No Sunbeam deployment found, bootstrapping a new cluster.")
        return bootstrap_local_cluster(manifest_path, roles)

    active_deployment = None
    for deployment in deployments:
        if deployment["name"] == active_deployment_name:
            active_deployment = deployment

    if not active_deployment:
        raise Exception("Unable to determine active Sunbeam deployment.")

    if active_deployment["type"] != "local":
        raise Exception("The active Sunbeam deployment is not a local deployment.")

    logging.info("Reusing local Sunbeam deployment: %s", active_deployment["name"])


def apply_manifest(
    destination_manifest_path: str,
    manifest_updates: dict,
    base_manifest_path: str | None = None,
    use_latest_manifest: bool = True,
):
    """Write a manifest to the specified location.

    Can receive a base manifest path and a dict of updates that
    will be applied on top.

    If "use_latest_manifest" is set and no base manifest is provided,
    the updates will be applied to the latest manifest from Sunbeam.
    """
    manifest = {}
    if base_manifest_path:
        with open(base_manifest_path) as f:
            manifest = yaml.safe_load(f)
    elif use_latest_manifest:
        manifest_data = get_latest_sunbeam_manifest()
        manifest = yaml.safe_load(io.StringIO(manifest_data or "")) or {}

    manifest = sunbeam_utils.merge_dict(manifest, manifest_updates)

    with open(destination_manifest_path, "w") as f:
        f.write(yaml.dump(manifest))


def get_latest_sunbeam_manifest() -> str:
    manifest = sunbeam_command("manifest show latest", capture_output=True)
    return str(manifest or "")


def get_sriov_numvfs(address: str) -> int:
    """Read configured VF capacity for a device."""
    path = f"/sys/bus/pci/devices/{address}/sriov_numvfs"
    with open(path, "r") as f:
        read_data = f.read()
    return int(read_data.strip())


def set_sriov_numvfs(address: str, num_vfs: int):
    """Read configured VF capacity for a device."""
    path = f"/sys/bus/pci/devices/{address}/sriov_numvfs"

    # In most cases, the number of VFs needs to be set to 0 before
    # being adjusted.
    subprocess.run(["sudo", "tee", path], input="0", text=True, check=True)
    subprocess.run(["sudo", "tee", path], input=str(num_vfs), text=True, check=True)


def is_sriov_capable(address: str) -> bool:
    """Determine whether a device is SR-IOV capable."""
    path = f"/sys/bus/pci/devices/{address}/sriov_totalvfs"
    return os.path.exists(path)


def get_iface_pci_address(ifname: str) -> str:
    """Determine the interface PCI address.

    :param: ifname: interface name
    :type: str
    :returns: the PCI address of the device.
    :rtype: str
    """
    net_dev_path = f"/sys/class/net/{ifname}/device"
    if not (os.path.exists(net_dev_path) and os.path.islink(net_dev_path)):
        # Not a PCI device.
        return ""
    resolved_path = os.path.realpath(net_dev_path)
    parts = resolved_path.split("/")
    if "virtio" in parts[-1]:
        return parts[-2]
    return parts[-1]


def is_hw_offload_available(ifname: str) -> bool:
    """Determine whether a devices supports switchdev hardware offload.

    :param: ifname: interface name
    :type: str
    :returns: whether device is SR-IOV capable or not
    :rtype: bool
    """
    phys_port_name_file = f"/sys/class/net/{ifname}/phys_port_name"
    if not os.path.isfile(phys_port_name_file):
        return False

    try:
        with open(phys_port_name_file, "r") as f:
            phys_port_name = f.readline().strip()
            return phys_port_name != ""
    except (OSError, IOError):
        return False


def ensure_hw_offload_enabled(ifname: str):
    cmd = ["sudo", "ethtool", "-K", ifname, "hw-tc-offload", "on"]
    subprocess.check_call(cmd, text=True)

    pci_address = get_iface_pci_address(ifname)
    cmd = [
        "sudo",
        "devlink",
        "dev",
        "eswitch",
        "set",
        f"pci/{pci_address}",
        "mode",
        "switchdev",
    ]
    subprocess.check_call(cmd, text=True)


def get_physfn_address(address: str) -> str:
    """Get the corresponding PF PCI address for a given VF."""
    path = f"/sys/bus/pci/devices/{address}/physfn"
    if not (os.path.exists(path) and os.path.islink(path)):
        # Not a VF.
        return ""
    resolved_path = os.path.realpath(path)
    return resolved_path.split("/")[-1]


def privileged_file_read(path: str):
    return subprocess.check_output(["sudo", "cat", path], text=True)


def parse_ovsdb_data(data) -> typing.Any:
    """Parse OVSDB data.

    https://tools.ietf.org/html/rfc7047#section-5.1
    """
    if isinstance(data, list) and len(data) == 2:
        if data[0] == "set":
            return [parse_ovsdb_data(element) for element in data[1]]
        if data[0] == "map":
            return {
                parse_ovsdb_data(key): parse_ovsdb_data(value) for key, value in data[1]
            }
        if data[0] == "uuid":
            return uuid.UUID(data[1])
    return data


def ovs_vsctl_list_table(table: str, record: str, columns: list[str] | None) -> dict:
    try:
        cmd = [
            "sudo",
            "openstack-hypervisor.ovs-vsctl",
            "--format",
            "json",
            "--if-exists",
        ]
        if columns:
            cmd += ["--columns=%s" % ",".join(columns)]
        cmd += ["list", table, record]
        out = subprocess.check_output(cmd).decode()
    except subprocess.CalledProcessError:
        # The columns may not exist.
        # --if-exists only applies to the record, not the columns.
        return {}

    raw_json = json.loads(out)
    headings = raw_json["headings"]
    data = raw_json["data"]

    parsed = {}
    # We've requested a single record.
    for record in data:
        for position, heading in enumerate(headings):
            parsed[heading] = parse_ovsdb_data(record[position])

    return parsed


def bitmask_to_core_list(core_bitmask: int) -> list[int]:
    """Convert a cpu id bitmask to a list of cpu ids."""
    idx = 0
    cores = []
    while core_bitmask:
        if core_bitmask % 2:
            cores.append(idx)
        idx += 1
        core_bitmask >>= 1
    return cores


def generate_cloud_config(path: str, is_admin=True):
    cloud_name = TEST_ADMIN_CLOUD_NAME if is_admin else TEST_DEMO_CLOUD_NAME
    admin_flag = "--admin" if is_admin else ""
    cmd = f"cloud-config -c {cloud_name} {admin_flag} -f {path} -u"
    sunbeam_command(cmd)


def create_sunbeam_demo_resources(manifest_path: str | None):
    cmd = "configure deployment --accept-defaults"
    if manifest_path:
        cmd += f" --manifest {manifest_path}"
    sunbeam_command(cmd)


def get_libvirt_domain_xml(domain_name: str) -> str:
    cmd = ["sudo", "openstack-hypervisor.virsh", "dumpxml", domain_name]
    return subprocess.check_output(cmd, text=True)


def add_secondary_cluster_node(node_fqdn: str) -> str:
    cmd = f"cluster add-secondary-region-node {node_fqdn}"
    out = sunbeam_command(cmd, capture_output=True)
    token = str(out).split(":")[-1].strip(" ")
    return token
