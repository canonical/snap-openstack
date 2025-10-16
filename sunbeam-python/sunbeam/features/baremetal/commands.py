# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue

import click
from rich.console import Console

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
)
from sunbeam.core.common import (
    read_config,
    update_config,
    update_status_background,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    JujuHelper,
    JujuWaitException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
)
from sunbeam.features.baremetal import constants
from sunbeam.features.interface.v1.openstack import (
    OpenStackControlPlaneFeature,
)
from sunbeam.utils import click_option_show_hints, pass_method_obj

LOG = logging.getLogger(__name__)
console = Console()


@click.command()
@click.argument("shard")
@click_option_show_hints
@pass_method_obj
def compute_shard_add(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    shard: str,
    show_hints: bool,
) -> None:
    """Add Ironic nova-compute shard."""
    items = {
        # item_name: charm_config
        shard: {"shard": shard},
    }
    _baremetal_resource_add(
        feature,
        deployment,
        constants.NOVA_IRONIC_SHARDS_TFVAR,
        items,
        "nova-ironic",
    )


@click.command()
@pass_method_obj
def compute_shard_list(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
) -> None:
    """List Ironic nova-compute shards."""
    _baremetal_resource_list(
        feature,
        deployment,
        constants.NOVA_IRONIC_SHARDS_TFVAR,
    )


@click.command()
@click.argument("shard")
@click_option_show_hints
@pass_method_obj
def compute_shard_delete(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    shard: str,
    show_hints: bool,
) -> None:
    """Delete Ironic nova-compute shard."""
    _baremetal_resource_delete(
        feature,
        deployment,
        constants.NOVA_IRONIC_SHARDS_TFVAR,
        shard,
        f"nova-ironic-{shard}",
    )


def _baremetal_resource_add(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    tfvars_key: str,
    items: dict[str, dict],
    charm_name_prefix: str,
    replace: bool = False,
) -> None:
    tfvars = _get_tfvars(feature, deployment)
    current_items = tfvars.get(tfvars_key, {})

    if replace:
        current_items = items
    else:
        for item, charm_config in items.items():
            if item in current_items:
                raise click.ClickException(f"Resource {item} already exists.")
            current_items[item] = charm_config

    tfvars[tfvars_key] = current_items

    apps = [f"{charm_name_prefix}-{suffix}" for suffix in items.keys()]
    _apply_tfvars(feature, deployment, tfvars, apps)


def _get_tfvars(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
) -> dict:
    client = deployment.get_client()
    config_key = feature.get_tfvar_config_key()

    try:
        tfvars = read_config(client, config_key)
    except ConfigItemNotFoundException:
        tfvars = {}

    return tfvars


def _apply_tfvars(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    tfvars: dict,
    apps: list[str],
) -> None:
    client = deployment.get_client()
    config_key = feature.get_tfvar_config_key()
    tfhelper = deployment.get_tfhelper(feature.tfplan)
    tfhelper.write_tfvars(tfvars)
    update_config(client, config_key, tfvars)

    try:
        tfhelper.apply()
    except TerraformException as ex:
        raise click.ClickException(
            f"Encountered exception while applying terraform plan: {ex}"
        )

    LOG.debug(f"Applications monitored for readiness: {apps}")
    status_queue: queue.Queue[str] = queue.Queue()
    task = update_status_background(feature, apps, status_queue)
    jhelper = JujuHelper(deployment.juju_controller)

    try:
        jhelper.wait_until_active(
            OPENSTACK_MODEL,
            apps,
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=status_queue,
        )
    except (JujuWaitException, TimeoutError):
        raise click.ClickException(f"Timed out waiting for {apps} to become active.")
    finally:
        task.stop()

    click.echo(f"Resource(s) {apps} added.")


def _baremetal_resource_list(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    tfvars_key: str,
) -> None:
    tfvars = _get_tfvars(feature, deployment)
    items = tfvars.get(tfvars_key, {})
    for item in items.keys():
        console.print(item)


def _baremetal_resource_delete(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    tfvars_key: str,
    item: str,
    charm_name: str,
) -> None:
    tfvars = _get_tfvars(feature, deployment)
    items = tfvars.get(tfvars_key, {})
    if item not in items:
        raise click.ClickException(f"Resource {item} doesn't exist.")

    items.pop(item)

    client = deployment.get_client()
    config_key = feature.get_tfvar_config_key()
    jhelper = JujuHelper(deployment.juju_controller)
    tfhelper = deployment.get_tfhelper(feature.tfplan)

    tfhelper.write_tfvars(tfvars)
    update_config(client, config_key, tfvars)

    try:
        tfhelper.apply()
    except TerraformException as ex:
        raise click.ClickException(
            f"Encountered exception while applying terraform plan: {ex}"
        )

    LOG.debug(f"Waiting for application to dissapear: {charm_name}")
    try:
        jhelper.wait_application_gone(
            [charm_name],
            OPENSTACK_MODEL,
            timeout=constants.IRONIC_APP_TIMEOUT,
        )
    except (JujuWaitException, TimeoutError):
        raise click.ClickException(f"Timed out waiting for {charm_name} to dissapear.")

    click.echo(f"Resource {item} deleted.")
