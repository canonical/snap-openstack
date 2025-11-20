# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import random

from sunbeam.core import juju

from . import utils


def test_region_controller(
    manifest_path,
    openstack_snap_channel,
):
    roles = ["region_controller"]
    utils.ensure_local_cluster_bootstrapped(
        manifest_path, openstack_snap_channel, roles
    )

    # Keystone and Horizon are expected to run on region controller nodes.
    controller = juju.JujuController(
        name="sunbeam-controller", api_endpoints=[], ca_cert="", is_external=False
    )
    juju_helper = juju.JujuHelper(controller)
    assert juju_helper.get_application("keystone", "openstack").is_active
    assert juju_helper.get_application("horizon", "openstack").is_active

    # Fetch and validate a token that can be used to create new regions.
    token = utils.add_secondary_cluster_node(
        "fake-node-%s.local" % random.randint(0, 100000)
    )
    region_controller_info = json.loads(base64.b64decode(token).decode())
    assert "juju_controller" in region_controller_info
    assert "api_endpoints" in region_controller_info["juju_controller"]
    assert "juju_registration_token" in region_controller_info
    assert "name" in region_controller_info
    assert "primary_region_name" in region_controller_info
