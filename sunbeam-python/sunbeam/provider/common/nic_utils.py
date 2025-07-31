# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from typing import Tuple

from sunbeam import devspec
from sunbeam.clusterd.client import Client
from sunbeam.core.juju import JujuHelper

LOG = logging.getLogger(__name__)


def fetch_nics(client: Client, node_name: str, jhelper: JujuHelper, model: str):
    LOG.debug("Fetching nics...")
    node = client.cluster.get_node_info(node_name)
    machine_id = str(node.get("machineid"))
    unit = jhelper.get_unit_from_machine("openstack-hypervisor", machine_id, model)
    action_result = jhelper.run_action(unit, model, "list-nics")
    return json.loads(action_result.get("result", "{}"))


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


def exclude_sriov_nic(
    node_name: str,
    nic: dict,
    pci_whitelist: list[dict],
    excluded_devices: dict[str, list],
):
    LOG.debug("Excluding SR-IOV nic: %s", nic["name"])
    if node_name not in excluded_devices:
        excluded_devices[node_name] = []
    if nic["pci_address"] not in excluded_devices[node_name]:
        excluded_devices[node_name].append(nic["pci_address"])
