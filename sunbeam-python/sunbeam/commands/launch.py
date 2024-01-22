# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import subprocess
from typing import Optional

import click
import openstack
import petname
from rich.console import Console
from snaphelpers import Snap

from sunbeam.clusterd.client import Client
from sunbeam.commands.configure import retrieve_admin_credentials
from sunbeam.commands.openstack import OPENSTACK_MODEL
from sunbeam.jobs.juju import JujuHelper, ModelNotFoundException, run_sync

LOG = logging.getLogger(__name__)
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
    name: Optional[str] = None,
) -> None:
    """Launch an OpenStack instance on demo setup"""

    data_location = snap.paths.user_data
    client: Client = ctx.obj
    jhelper = JujuHelper(client, data_location)
    with console.status("Fetching user credentials ... "):
        try:
            run_sync(jhelper.get_model(OPENSTACK_MODEL))
        except ModelNotFoundException:
            LOG.error(f"Expected model {OPENSTACK_MODEL} missing")
            raise click.ClickException("Please run `sunbeam cluster bootstrap` first")

        admin_auth_info = retrieve_admin_credentials(jhelper, OPENSTACK_MODEL)

        terraform_plan_location = snap.paths.user_common / "etc" / "demo-setup"
        if not terraform_plan_location.exists():
            raise click.ClickException("Please run `sunbeam configure` first")

        try:
            terraform = str(snap.paths.snap / "bin" / "terraform")
            cmd = [terraform, "output", "-json"]
            LOG.debug(f'Running command {" ".join(cmd)}')
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=terraform_plan_location,
            )
            tf_output = json.loads(process.stdout)

        except subprocess.CalledProcessError:
            LOG.exception("Error initializing Terraform")
            raise click.ClickException("Please run `sunbeam configure` first")

    console.print("Launching an OpenStack instance ... ")
    try:
        conn = openstack.connect(
            auth_url=admin_auth_info.get("OS_AUTH_URL"),
            username=tf_output["OS_USERNAME"]["value"],
            password=tf_output["OS_PASSWORD"]["value"],
            project_name=tf_output["OS_PROJECT_NAME"]["value"],
            user_domain_name=tf_output["OS_USER_DOMAIN_NAME"]["value"],
            project_domain_name=tf_output["OS_PROJECT_DOMAIN_NAME"]["value"],
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
            network = conn.network.find_network(
                f'{tf_output["OS_USERNAME"]["value"]}-network'
            )
            keypair = conn.compute.find_keypair(key)
            server = conn.compute.create_server(
                name=instance_name,
                image_id=image.id,
                flavor_id=flavor.id,
                networks=[{"uuid": network.id}],
                key_name=keypair.name,
            )

            server = conn.compute.wait_for_server(server)
            server_id = server.id
        except openstack.exceptions.SDKException as e:
            LOG.error(f"Instance creation request failed: {e}")
            raise click.ClickException(
                "Unable to request new instance. Please run `sunbeam configure` first."
            )

    with console.status("Allocating IP address to instance ... "):
        try:
            external_network = conn.network.find_network("external-network")
            ip_ = conn.network.create_ip(floating_network_id=external_network.id)
            conn.compute.add_floating_ip_to_server(server_id, ip_.floating_ip_address)
            console.print(
                "Access instance with",
                f"`ssh -i {key_path} ubuntu@{ip_.floating_ip_address}`",
            )
        except openstack.exceptions.SDKException as e:
            LOG.error(f"Error allocating IP address: {e}")
            raise click.ClickException(
                "Could not allocate IP address. Check your configuration."
            )
