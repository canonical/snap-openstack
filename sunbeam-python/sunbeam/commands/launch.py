# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import typing

import click
import petname  # type: ignore [import-untyped]
from rich.console import Console
from snaphelpers import Snap

from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import TerraformException
from sunbeam.lazy import LazyImport

if typing.TYPE_CHECKING:
    import openstack
else:
    openstack = LazyImport("openstack")

LOG = logging.getLogger(__name__)
INSTANCE_WAIT_TIMEOUT = 360  # 6 min
console = Console()
snap = Snap()


@click.command()
@click.argument(
    "image_name",
    default="ubuntu",
)
@click.option(
    "-k",
    "--key",
    default="sunbeam",
    help="""
The name of the SSH key in OpenStack to use for the instance.
Creates a new key in ~/snap/openstack/current/ if the key does not exist in OpenStack.
""",
)
@click.option("-n", "--name", help="The name for the instance.")
@click.pass_context
def launch(
    ctx: click.Context,
    image_name: str,
    key: str,
    name: str | None = None,
) -> None:
    """Launch an OpenStack instance on demo setup."""
    snap = Snap()
    data_location = snap.paths.user_data
    deployment: Deployment = ctx.obj

    is_bootstrapped = deployment.get_client().cluster.check_sunbeam_bootstrapped()
    if not is_bootstrapped:
        raise click.ClickException("Please run `sunbeam cluster bootstrap` first.")

    compute_nodes = deployment.get_client().cluster.list_nodes_by_role("compute")
    if not compute_nodes:
        raise click.ClickException("No compute role found. Cannot launch instance.")

    jhelper = JujuHelper(deployment.juju_controller)

    with console.status("Fetching user credentials ... "):
        if not jhelper.model_exists(OPENSTACK_MODEL):
            LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
            raise click.ClickException(
                f"Cannot find {OPENSTACK_MODEL}. Please destroy and re-bootstrap."
            )

        admin_auth_info = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)

        tfplan = "demo-setup"
        tfhelper = deployment.get_tfhelper(tfplan)
        try:
            tf_output = tfhelper.output(hide_output=True)
        except TerraformException:
            LOG.debug("Failed to load credentials from terraform", exc_info=True)
            raise click.ClickException(
                "Failed to load user credentials from deployment. See logs for details."
            )

    console.print("Launching an OpenStack instance ... ")
    try:
        conn = openstack.connect(
            auth_url=admin_auth_info.get("OS_AUTH_URL"),
            username=tf_output["OS_USERNAME"],
            password=tf_output["OS_PASSWORD"],
            project_name=tf_output["OS_PROJECT_NAME"],
            user_domain_name=tf_output["OS_USER_DOMAIN_NAME"],
            project_domain_name=tf_output["OS_PROJECT_DOMAIN_NAME"],
            cacert=admin_auth_info.get("OS_CACERT"),
        )
    except openstack.exceptions.SDKException:
        LOG.error("Could not authenticate to Keystone.")
        raise click.ClickException("Unable to connect to OpenStack")

    with console.status("Checking for SSH key pair ... ") as status:
        key_path = f"{data_location}/{key}"
        status.update("Checking for SSH public key in OpenStack ... ")
        try:
            conn.compute.get_keypair(key)
            console.print(f"Found {key} key in OpenStack!")
        except openstack.exceptions.ResourceNotFound:
            status.update(
                f"No {key} key found in OpenStack. Creating SSH key at {key_path}"
            )
            key_id = conn.compute.create_keypair(name=key)
            with open(key_path, "w", encoding="utf-8") as key_file:
                os.fchmod(key_file.fileno(), 0o600)
                key_file.write(key_id.private_key)

    with console.status("Creating the OpenStack instance ... "):
        try:
            instance_name = name if name else petname.Generate()
            image = conn.compute.find_image(image_name)
            flavor = conn.compute.find_flavor("m1.tiny")
            network = conn.network.find_network(f"{tf_output['OS_USERNAME']}-network")
            keypair = conn.compute.find_keypair(key)
            server = conn.compute.create_server(
                name=instance_name,
                image_id=image.id,
                flavor_id=flavor.id,
                networks=[{"uuid": network.id}],
                key_name=keypair.name,
            )

            server = conn.compute.wait_for_server(server, wait=INSTANCE_WAIT_TIMEOUT)
        except openstack.exceptions.SDKException as e:
            LOG.error(f"Instance creation request failed: {e}")
            raise click.ClickException(
                "Unable to request new instance. Please run `sunbeam configure` first."
            )

    with console.status("Allocating IP address to instance ... "):
        try:
            external_network = conn.network.find_network("external-network")
            ip_ = conn.network.create_ip(floating_network_id=external_network.id)
            conn.add_ips_to_server(server, ips=[ip_.floating_ip_address])
            console.print("Access the instance by running the following command:")
            console.print(
                f"`ssh -i {key_path} ubuntu@{ip_.floating_ip_address}`",
            )
        except openstack.exceptions.SDKException as e:
            LOG.error(f"Error allocating IP address: {e}")
            raise click.ClickException(
                "Could not allocate IP address. Check your configuration."
            )
