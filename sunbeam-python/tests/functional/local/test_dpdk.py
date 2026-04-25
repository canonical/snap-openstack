# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import subprocess

from . import utils

LOG = logging.getLogger(__name__)


def test_dpdk(
    tmp_path,
    manifest_path,
    openstack_admin_session,
    openstack_demo_session,
    sunbeam_flavor_huge_pages,
    request,
):
    manifest_updates = {
        "core": {
            "config": {
                "dpdk": {
                    "enabled": "true",
                    "datapath_cores": 1,
                    "control_plane_cores": 1,
                    "memory": 1024,
                    "driver": "vfio-pci",
                    "ports": {},
                }
            }
        }
    }
    dpdk_manifest_path = tmp_path / "dpdk-manifest.yaml"
    utils.apply_manifest(
        destination_manifest_path=str(dpdk_manifest_path),
        manifest_updates=manifest_updates,
        base_manifest_path=manifest_path,
    )

    LOG.info("Applying DPDK configuration")
    utils.sunbeam_command(
        f"-v configure dpdk -m {dpdk_manifest_path} --accept-defaults"
    )

    # It may take a few moments for DPDK to be initialized. As such,
    # we'll perform a few retries.
    dpdk_init_retries = 12
    dpdk_init_check_interval = 5
    dpdk_initialized = False
    for attempt in range(dpdk_init_retries):
        dpdk_initialized = (
            subprocess.check_output(
                [
                    "sudo",
                    "openstack-hypervisor.ovs-vsctl",
                    "get",
                    "Open_vSwitch",
                    ".",
                    "dpdk_initialized",
                ],
                text=True,
            ).strip()
            == "true"
        )
        if dpdk_initialized:
            LOG.info("OVS DPDK initialized")
            break
        else:
            LOG.info("OVS DPDK not initialized yet")
            if attempt < dpdk_init_retries - 1:
                LOG.debug("Rechecking in %d seconds", dpdk_init_check_interval)
                time.sleep(dpdk_init_check_interval)
    assert dpdk_initialized, "OVS DPDK did not initialize in time."

    ovs_config = utils.ovs_vsctl_list_table("Open_vSwitch", ".", ["other-config"])
    assert ovs_config["other_config"]["dpdk-init"] == "try"

    dpdk_memory = ovs_config["other_config"]["dpdk-socket-mem"]
    dpdk_lcore_mask = ovs_config["other_config"]["dpdk-lcore-mask"]
    pmd_cpu_mask = ovs_config["other_config"]["dpdk-lcore-mask"]

    assert 1 == len(utils.bitmask_to_core_list(int(dpdk_lcore_mask, 16)))
    assert 1 == len(utils.bitmask_to_core_list(int(pmd_cpu_mask, 16)))

    dpdk_memory_numa = dpdk_memory.split(",")
    assert dpdk_memory_numa[0] == "1024"
    if len(dpdk_memory_numa) > 0:
        for mem_numa in dpdk_memory_numa[1:]:
            assert mem_numa == "0"

    instance_name = "sunbeam-dpdk-test"
    instance = openstack_demo_session.create_server(
        instance_name,
        image="ubuntu",
        flavor=sunbeam_flavor_huge_pages,
        network="demo-network",
        wait=True,
    )
    request.addfinalizer(lambda: openstack_demo_session.delete_server(instance.id))

    # admin view of the instance, including libvirt domain name
    admin_instance = openstack_admin_session.get_server(instance.id, all_projects=True)
    libvirt_domain_name = admin_instance.instance_name

    domain_xml = utils.get_libvirt_domain_xml(libvirt_domain_name)
    # We won't parse the xml for now, we just want to ensure that there's a
    # vhost-user interface attached that uses the dpdk data path.
    assert "<interface type='vhostuser'>" in domain_xml
