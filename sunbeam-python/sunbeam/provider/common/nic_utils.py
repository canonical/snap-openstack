# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from typing import Tuple

from sunbeam import devspec
from sunbeam.clusterd.client import Client
from sunbeam.core.juju import JujuHelper, JujuStepHelper

LOG = logging.getLogger(__name__)


def fetch_nics(client: Client, node_name: str, jhelper: JujuHelper, model: str):
    LOG.debug("Fetching nics")
    node = client.cluster.get_node_info(node_name)
    machine_id = str(node.get("machineid"))
    unit = jhelper.get_unit_from_machine("openstack-hypervisor", machine_id, model)
    action_result = jhelper.run_action(unit, model, "list-nics")
    return json.loads(action_result.get("result", "{}"))


def fetch_nics_from_subordinate(
    client: Client,
    node_name: str,
    jhelper: JujuHelper,
    model: str,
    principal_app: str,
    subordinate_app: str,
):
    LOG.debug("Fetching nics from subordinate")
    node = client.cluster.get_node_info(node_name)
    machine_id = str(node.get("machineid"))
    principal_unit = jhelper.get_unit_from_machine(principal_app, machine_id, model)
    jstephelper = JujuStepHelper()
    jstephelper.jhelper = jhelper
    subordinate_unit = jstephelper.find_subordinate_unit_for(
        principal_unit, subordinate_app, model
    )
    action_result = jhelper.run_action(subordinate_unit, model, "list-nics")
    return json.loads(action_result.get("result", "{}"))


def fetch_gpus(client: Client, node_name: str, jhelper: JujuHelper, model: str):
    LOG.debug("Fetching gpus")
    node = client.cluster.get_node_info(node_name)
    machine_id = str(node.get("machineid"))
    unit = jhelper.get_unit_from_machine("openstack-hypervisor", machine_id, model)
    action_result = jhelper.run_action(unit, model, "list-gpus")
    return json.loads(action_result.get("result", "{}"))


def get_nic_str_repr(nic: dict):
    """Get the string representation for a nic retrieved through list-nics."""
    vendor = nic.get("vendor_name") or nic.get("vendor_id") or "Unknown vendor"
    product = nic.get("product_name") or nic.get("product_id") or "Unknown product"
    name = nic.get("name") or "Unknown ifname"
    return f"{vendor} {product} ({name})"


def is_sriov_nic_whitelisted(
    node_name: str,
    nic: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
) -> Tuple[bool, str | None]:
    """Returns the (is_whitelisted>, physnet) tuple."""
    pci_address = nic["pci_address"]

    node_excluded_devices = excluded_devices.get(node_name) or []
    if pci_address in node_excluded_devices:
        return False, None

    for spec_dict in pci_whitelist:
        if not isinstance(spec_dict, dict):
            raise ValueError("Invalid device spec, expecting a dict: %s." % spec_dict)

        pci_spec = devspec.PciDeviceSpec(spec_dict)
        dev = {
            "vendor_id": nic["vendor_id"].replace("0x", ""),
            "product_id": nic["product_id"].replace("0x", ""),
            "address": nic["pci_address"],
            "parent_addr": nic["pf_pci_address"],
        }
        match = pci_spec.match(dev)
        if match:
            return True, spec_dict.get("physical_network")

    return False, None


def is_pci_device_whitelisted(
    node_name: str,
    device: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
) -> bool:
    """Returns True if pci device is whitelisted."""
    pci_address = device["pci_address"]

    node_excluded_devices = excluded_devices.get(node_name) or []
    if pci_address in node_excluded_devices:
        return False

    for spec_dict in pci_whitelist:
        if not isinstance(spec_dict, dict):
            raise ValueError("Invalid device spec, expecting a dict: %s." % spec_dict)

        pci_spec = devspec.PciDeviceSpec(spec_dict)
        dev = {
            "vendor_id": device["vendor_id"].replace("0x", ""),
            "product_id": device["product_id"].replace("0x", ""),
            "address": device["pci_address"],
        }
        match = pci_spec.match(dev)
        if match:
            return True

    return False


def whitelist_pci_passthrough_device(
    node_name: str,
    device: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
):
    pci_address = device["pci_address"]
    LOG.debug("Whitelisting PCI device: %s", pci_address)

    node_excluded_devices = excluded_devices.get(node_name) or []
    if pci_address in node_excluded_devices:
        # If user excludes a PCI device via manifest, do not add
        # the device in pci_whitelist
        LOG.debug("PCI device excluded: %s", pci_address)
        return

    # Update the global whitelist if needed.
    whitelisted = is_pci_device_whitelisted(
        node_name, device, pci_whitelist, excluded_devices
    )
    if not whitelisted:
        new_dev_spec = {
            "address": device["pci_address"],
            "vendor_id": device["vendor_id"].replace("0x", ""),
            "product_id": device["product_id"].replace("0x", ""),
        }
        pci_whitelist.append(new_dev_spec)
    else:
        LOG.debug("PCI device already whitelisted: %s", pci_address)


def whitelist_sriov_nic(
    node_name: str,
    nic: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
    physnet: str | None,
):
    LOG.debug("Whitelisting SR-IOV nic: %s %s", nic["name"], nic["pci_address"])
    pci_address = nic["pci_address"]

    node_excluded_devices = excluded_devices.get(node_name) or []
    if pci_address in node_excluded_devices:
        LOG.debug(
            "Removing SR-IOV nic from the exclusion list: %s %s",
            nic["name"],
            nic["pci_address"],
        )
        node_excluded_devices.remove(pci_address)

    # Update the global whitelist if needed.
    whitelisted = is_sriov_nic_whitelisted(
        node_name, nic, pci_whitelist, excluded_devices
    )[0]
    if not whitelisted:
        # Openstack expects this to be null when using hw offloading
        # with overlay networks.
        # https://docs.openstack.org/neutron/latest/admin/config-ovs-offload.html
        if not physnet:
            physnet = None
        elif physnet.lower() in ("none", "null", "no-physnet"):
            physnet = None

        new_dev_spec = {
            "address": nic["pci_address"],
            "vendor_id": nic["vendor_id"].replace("0x", ""),
            "product_id": nic["product_id"].replace("0x", ""),
            "physical_network": physnet,
        }
        pci_whitelist.append(new_dev_spec)
    else:
        LOG.debug(
            "SR-IOV nic already whitelisted: %s %s", nic["name"], nic["pci_address"]
        )


def whitelist_remote_managed_vf(
    node_name: str,
    vf_nic: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
    physnet: str | None,
):
    """Whitelist a DPU-backed SR-IOV virtual function as a remote-managed device.

    For off-path SmartNIC DPUs the networking control plane runs on the DPU,
    while Nova on the hypervisor host claims the virtual functions (VFs). Nova
    matches these VFs by PCI ``address``, ``vendor_id``/``product_id`` and
    requires the ``remote_managed`` tag. The MAAS physnet tag only selects the
    PF; remote-managed VF specs always use a null physical network.

    https://docs.openstack.org/neutron/2024.1/admin/ovn/smartnic_dpu.html
    """
    LOG.debug(
        "Whitelisting remote-managed VF: %s %s",
        vf_nic.get("name"),
        vf_nic.get("pci_address"),
    )
    pci_address = vf_nic["pci_address"]

    node_excluded_devices = excluded_devices.get(node_name) or []
    if pci_address in node_excluded_devices:
        LOG.debug(
            "Removing remote-managed VF from the exclusion list: %s %s",
            vf_nic.get("name"),
            pci_address,
        )
        node_excluded_devices.remove(pci_address)

    physnet = None

    new_dev_spec = {
        "address": pci_address,
        "vendor_id": vf_nic["vendor_id"].replace("0x", ""),
        "product_id": vf_nic["product_id"].replace("0x", ""),
        "physical_network": physnet,
        "remote_managed": "true",
    }

    # Avoid exact duplicates if the same VF is encountered more than once.
    if new_dev_spec in pci_whitelist:
        LOG.debug(
            "Remote-managed VF spec already whitelisted: %s/%s (physnet: %s)",
            new_dev_spec["vendor_id"],
            new_dev_spec["product_id"],
            physnet,
        )
        return

    pci_whitelist.append(new_dev_spec)


def exclude_sriov_nic(
    node_name: str,
    nic: dict,
    excluded_devices: dict[str, list],
):
    LOG.debug("Excluding SR-IOV nic: %s", nic["name"])
    if node_name not in excluded_devices:
        excluded_devices[node_name] = []
    if nic["pci_address"] not in excluded_devices[node_name]:
        excluded_devices[node_name].append(nic["pci_address"])
