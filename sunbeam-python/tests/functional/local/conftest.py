# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import os

import openstack
import pytest
from openstack import exceptions as openstack_exc

from . import utils


def pytest_addoption(parser):
    parser.addoption(
        "--manifest-path",
        action="store",
        help=(
            "Sunbeam manifest used for testing purposes. "
            "Modified versions of it will be passed during bootstrap "
            "and other Sunbeam operations."
        ),
    )
    parser.addoption(
        "--openstack-snap-channel",
        action="store",
        help="The snap channel used when installing the Openstack snap.",
        default="2024.1/edge",
    )
    parser.addoption(
        "--sriov-interface-name",
        action="store",
        help="The name of a network interface used for SR-IOV testing.",
    )
    parser.addoption(
        "--physnet",
        action="store",
        help="The default Neutron physical network used during the tests.",
        default="physnet1",
    )


@pytest.fixture(scope="session")
def manifest_path(request) -> str:
    return request.config.getoption("manifest_path")


@pytest.fixture(scope="session")
def openstack_snap_channel(request) -> str:
    return request.config.getoption("openstack_snap_channel")


@pytest.fixture(scope="session")
def sriov_interface_name(request) -> str:
    return request.config.getoption("sriov_interface_name")


@pytest.fixture(scope="session")
def physnet(request) -> str:
    return request.config.getoption("physnet")


@pytest.fixture(scope="session")
def ensure_local_cluster_bootstrapped(manifest_path, openstack_snap_channel):
    utils.ensure_local_cluster_bootstrapped(manifest_path, openstack_snap_channel)


@pytest.fixture(scope="session")
def cloud_config_path(tmpdir_factory) -> str:
    # Can't use the "tmp_path" fixture, we'd like this to be session scoped.
    path = tmpdir_factory.mktemp("config").join("clouds.yaml")
    return str(path)


@pytest.fixture(scope="session")
def openstack_admin_session(cloud_config_path, ensure_local_cluster_bootstrapped):
    utils.generate_cloud_config(cloud_config_path, is_admin=True)

    os.environ["OS_CLIENT_CONFIG_FILE"] = cloud_config_path
    return openstack.connect(cloud=utils.TEST_ADMIN_CLOUD_NAME)


@pytest.fixture(scope="session")
def openstack_demo_session(
    cloud_config_path,
    ensure_local_cluster_bootstrapped,
    openstack_admin_session,
    manifest_path,
):
    demo_project = None
    try:
        demo_project = openstack_admin_session.get_project("demo")
    except openstack_exc.NotFoundException:
        pass

    if not demo_project:
        utils.create_sunbeam_demo_resources(manifest_path)

    utils.generate_cloud_config(cloud_config_path, is_admin=False)

    os.environ["OS_CLIENT_CONFIG_FILE"] = cloud_config_path
    return openstack.connect(cloud=utils.TEST_DEMO_CLOUD_NAME)


@pytest.fixture(scope="function")
def sunbeam_flavor_huge_pages(openstack_admin_session) -> str:
    """Temporarily enable huge pages using flavor extra specs."""
    flavor_name = "test-hugepages"

    if not openstack_admin_session.get_flavor(flavor_name):
        flavor = openstack_admin_session.create_flavor(
            flavor_name,
            ram=1024,
            vcpus=1,
            disk=4,
            description="Sunbeam test flavor (huge pages)",
        )
        openstack_admin_session.set_flavor_specs(
            flavor.id, {"hw:mem_page_size": "large"}
        )

    return flavor_name
