# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from xml import etree as xml_etree

import pytest

from . import snap, utils

LOG = logging.getLogger(__name__)

# The number of VFs to create if there aren't any existing ones.
DEFAULT_NUM_VFS = 4


def test_sriov(
    tmp_path,
    manifest_path,
    physnet,
    sriov_interface_name: str,
    openstack_admin_session,
    openstack_demo_session,
    request,
):
    if not sriov_interface_name:
        pytest.skip("No SR-IOV interface specified, skipping SR-IOV tests.")

    pci_address = utils.get_iface_pci_address(sriov_interface_name)
    LOG.info("Detected SR-IOV interface PCI address: %s", pci_address)

    if not utils.is_sriov_capable(pci_address):
        raise Exception(
            "The specified interface name does not support SR-IOV: %s"
            % sriov_interface_name
        )

    has_hw_offload = utils.is_hw_offload_available(sriov_interface_name)
    LOG.info("Hardware offloading available: %s", has_hw_offload)

    if has_hw_offload:
        utils.ensure_hw_offload_enabled(sriov_interface_name)

    num_vfs = utils.get_sriov_numvfs(pci_address)
    if num_vfs <= 0:
        num_vfs = DEFAULT_NUM_VFS
        LOG.info("No SR-IOV VFs defined, creating %s", num_vfs)
        utils.set_sriov_numvfs(pci_address, num_vfs)

    # The device specs are expected to contain a physnet unless overlay
    # networks are used (requires hardware offloading).
    if has_hw_offload:
        physnet = None
    manifest_updates = {
        "core": {
            "config": {
                "pci": {
                    "device_specs": [
                        {
                            "address": pci_address,
                            "physical_network": physnet,
                        },
                    ]
                }
            }
        }
    }
    sriov_manifest_path = tmp_path / "sriov-manifest.yaml"
    utils.apply_manifest(
        destination_manifest_path=str(sriov_manifest_path),
        manifest_updates=manifest_updates,
        base_manifest_path=manifest_path,
    )

    LOG.info("Applying SR-IOV configuration.")
    utils.sunbeam_command(
        f"-v configure sriov -m {sriov_manifest_path} --accept-defaults"
    )

    snap_cache = snap.SnapCache()
    openstack_hypervisor_snap = snap_cache["openstack-hypervisor"]

    assert "neutron-sriov-nic-agent" in openstack_hypervisor_snap.services
    sriov_agent_service = openstack_hypervisor_snap.services["neutron-sriov-nic-agent"]
    # If hardware offloading is available, the VFs are handled by the
    # OVN mechanism driver. If not, the SR-IOV mechanism driver will
    # bind the ports, leveraging the SR-IOV nic agent.
    sriov_agent_expected = not has_hw_offload
    assert sriov_agent_service["enabled"] == sriov_agent_expected

    whitelisted_vfs = _get_whitelisted_vfs(pci_address)
    assert num_vfs == len(whitelisted_vfs)

    instance_name = "sunbeam-sriov-test"
    instance = openstack_demo_session.create_server(
        instance_name,
        image="ubuntu",
        flavor="m1.tiny",
        network="demo-network",
        wait=True,
    )
    request.addfinalizer(lambda: openstack_demo_session.delete_server(instance.id))

    # admin view of the instance, including libvirt domain name
    admin_instance = openstack_admin_session.get_server(instance.id, all_projects=True)
    libvirt_domain_name = admin_instance.instance_name

    if has_hw_offload:
        # The demo network is expected to be an overlay network, it can
        # be used with SR-IOV only if hardware offloading is available.
        sriov_network_name = "demo-network"
    else:
        sriov_network_name = "external-network"

    demo_project = openstack_admin_session.get_project("demo")
    sriov_network = openstack_admin_session.get_network(sriov_network_name)
    port_kwargs = {
        "binding:vnic_type": "direct",
        "network_id": sriov_network.id,
        "project_id": demo_project.id,
    }
    # We need to use the admin project in order to create ports on the
    # external network.
    sriov_port = openstack_admin_session.create_port(**port_kwargs)
    openstack_admin_session.compute.create_server_interface(
        server=admin_instance, port_id=sriov_port.id
    )

    domain_xml = utils.get_libvirt_domain_xml(libvirt_domain_name)
    # mypy can't detect xml_etree.ElementTree, we'll tell it to ignore
    # the following line.
    domain_element = xml_etree.ElementTree.fromstring(domain_xml)  # type: ignore[attr-defined]
    hostdev_iface_elements = domain_element.findall(
        "./devices/interface[@type='hostdev']"
    )
    assert len(hostdev_iface_elements) == 1
    hostdev_iface_element = hostdev_iface_elements[0]
    hostdev_source_address = hostdev_iface_element.find("source/address").attrib
    assert hostdev_source_address["type"] == "pci"
    hostdev_source_address_str = "%04x:%02x:%02x.%x" % tuple(
        int(x, 16)
        for x in [
            hostdev_source_address["domain"],
            hostdev_source_address["bus"],
            hostdev_source_address["slot"],
            hostdev_source_address["function"],
        ]
    )
    assert hostdev_source_address_str in whitelisted_vfs


def _get_nova_conf_device_specs() -> list[dict]:
    nova_conf_raw = utils.privileged_file_read(
        "/var/snap/openstack-hypervisor/common/etc/nova/nova.conf"
    )
    device_specs = []
    for line in nova_conf_raw.split("\n"):
        if line.startswith("device_spec = "):
            spec_json = line.replace("device_spec = ", "")
            spec = json.loads(spec_json)
            device_specs.append(spec)
    return device_specs


def _get_whitelisted_vfs(pf_address):
    device_specs = _get_nova_conf_device_specs()

    # The Nova PCI whitelist may contain wildcards, however
    # Sunbeam will pass the exact address of whitelisted VFs.
    found_vfs = []
    for spec in device_specs:
        spec_address = spec.get("address")
        if not spec_address:
            continue
        parent_pf_address = utils.get_physfn_address(spec_address)
        if parent_pf_address != pf_address:
            continue
        # Found a matching vf.
        found_vfs.append(spec_address)

    return found_vfs
