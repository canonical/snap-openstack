# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import subprocess

import pytest

from . import utils


@pytest.fixture(scope="module")
def ensure_network_node_cluster(manifest_path, openstack_snap_channel):
    """Bootstrap a cluster with network role.

    Network and compute roles are mutually exclusive.
    """
    info = utils.get_sunbeam_deployments() or {}
    deployments = info.get("deployments") or []

    if deployments:
        logging.warning(
            "Until the ability to run multi-node functional tests is added, "
            "skip the tests if there's an existing deployment."
        )
        return

    utils.ensure_openstack_snap_installed(openstack_snap_channel)
    utils.install_bootstrap_prerequisites()

    logging.info("Bootstrapping cluster with network node role.")
    cmd = "cluster bootstrap --accept-defaults --role network"
    if manifest_path:
        cmd += f" --manifest {manifest_path}"
    utils.sunbeam_command(cmd)


def test_network_node(
    tmp_path,
    manifest_path,
    ensure_network_node_cluster,
):
    manifest_updates = {
        "core": {
            "config": {
                "external_network": {
                    "nic": "eth1",
                }
            }
        }
    }

    network_manifest_path = tmp_path / "network-node-manifest.yaml"
    utils.apply_manifest(
        destination_manifest_path=str(network_manifest_path),
        manifest_updates=manifest_updates,
        base_manifest_path=manifest_path,
    )

    logging.info("Configuring network node deployment.")
    utils.sunbeam_command(
        f"-v configure deployment -m {network_manifest_path} --accept-defaults"
    )

    # Network nodes use microovn.ovs-vsctl instead of openstack-hypervisor.ovs-vsctl
    cmd = [
        "sudo",
        "microovn.ovs-vsctl",
        "--format",
        "json",
        "--if-exists",
        "--columns=external_ids",
        "list",
        "Open_vSwitch",
        ".",
    ]
    out = subprocess.check_output(cmd).decode()
    raw_json = json.loads(out)
    headings = raw_json["headings"]
    data = raw_json["data"]

    external_ids = {}
    for record in data:
        for position, heading in enumerate(headings):
            if heading == "external_ids":
                external_ids = utils.parse_ovsdb_data(record[position])

    logging.info("Checking OVN configuration.")

    # Verify bridge mappings
    assert "ovn-bridge-mappings" in external_ids
    bridge_mappings = external_ids["ovn-bridge-mappings"]
    assert "physnet1" in bridge_mappings
    assert ":" in bridge_mappings

    # Verify chassis MAC mappings
    assert "ovn-chassis-mac-mappings" in external_ids
    mac_mappings = external_ids["ovn-chassis-mac-mappings"]
    assert "physnet1" in mac_mappings
    assert ":" in mac_mappings

    # Verify CMS options include gateway enablement
    assert "ovn-cms-options" in external_ids
    cms_options = external_ids["ovn-cms-options"]
    assert "enable-chassis-as-gw" in cms_options

    # Verify encapsulation settings
    assert "ovn-encap-type" in external_ids
    assert external_ids["ovn-encap-type"] == "geneve"
