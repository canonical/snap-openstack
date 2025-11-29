# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import queue
import typing

import click
from rich import box
from rich.console import Console
from rich.table import Column, Table

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
    ActionFailedException,
    JujuHelper,
    JujuSecretNotFound,
    JujuWaitException,
    LeaderNotFoundException,
)
from sunbeam.core.openstack import OPENSTACK_MODEL
from sunbeam.core.terraform import (
    TerraformException,
)
from sunbeam.features.baremetal import constants, feature_config
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


@click.command()
@click.argument("group_name")
@click_option_show_hints
@pass_method_obj
def conductor_group_add(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    group_name: str,
    show_hints: bool,
) -> None:
    """Add ironic-conductor group."""
    items = {
        # item_name: charm_config
        group_name: {"conductor-group": group_name},
    }
    _baremetal_resource_add(
        feature,
        deployment,
        constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
        items,
        "ironic-conductor",
        apps_desired_status=["active", "blocked"],
    )
    _run_set_temp_url_secret(
        feature,
        deployment,
        [f"ironic-conductor-{group_name}"],
    )


@click.command()
@pass_method_obj
def conductor_group_list(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
) -> None:
    """List ironic-conductor groups."""
    _baremetal_resource_list(
        feature,
        deployment,
        constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
    )


@click.command()
@click.argument("group_name")
@click_option_show_hints
@pass_method_obj
def conductor_group_delete(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    group_name: str,
    show_hints: bool,
) -> None:
    """Delete ironic-conductor group."""
    _baremetal_resource_delete(
        feature,
        deployment,
        constants.IRONIC_CONDUCTOR_GROUPS_TFVAR,
        group_name,
        f"ironic-conductor-{group_name}",
    )


def _switch_config_secret_name(name: str) -> str:
    return f"switch-config-{name}"


@click.command()
@click.argument("protocol", type=click.Choice(["netconf", "generic"]))
@click.argument("name")
@click.option(
    "--config",
    required=True,
    type=click.File("r"),
    help="The path to a baremetal / generic switch config file.",
)
@click.option(
    "--additional-file",
    multiple=True,
    type=(str, click.File("r")),
    help="The path to an additional file. Can be repeated for multiple files",
)
@click_option_show_hints
@pass_method_obj
def switch_config_add(*args, **kwargs):
    """Add Neutron baremetal / generic switch configuration."""
    _switch_config_add(*args, **kwargs)


def _switch_config_add(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    protocol: str,
    name: str,
    config: typing.TextIO,
    additional_file: list[tuple[str, typing.TextIO]],
    show_hints: bool,
) -> None:
    secret_name = _switch_config_secret_name(name)
    jhelper = JujuHelper(deployment.juju_controller)
    if jhelper.secret_exists(OPENSTACK_MODEL, secret_name):
        raise click.ClickException(f"Secret {name} already exists.")

    config_obj = _read_switch_config(protocol, name, config, additional_file)

    # Create secret and grant it to the config charm and neutron.
    secret_data = {
        "conf": config_obj.configfile,
        **config_obj.additional_files,
    }
    secret_id = jhelper.add_secret(
        OPENSTACK_MODEL,
        secret_name,
        secret_data,
        "Neutron switch config",
    )

    config_charm = "neutron-baremetal-switch-config"
    if protocol == "generic":
        config_charm = "neutron-generic-switch-config"

    for app in ["neutron", config_charm]:
        jhelper.grant_secret(OPENSTACK_MODEL, secret_name, app)

    # Update charm's "conf-secrets" config.
    tfvars_key = constants.SWITCH_CONFIG_TFVAR[protocol]
    tfvars = _get_tfvars(feature, deployment)

    val = tfvars.get(tfvars_key)
    val = ",".join([val, secret_id]) if val else secret_id
    tfvars[tfvars_key] = val

    # update list of secrets.
    tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
    conf_secrets = tfvars.get(tfvars_key, {})
    secret_list = conf_secrets.get(protocol, [])

    secret_list.append(secret_name)
    conf_secrets[protocol] = secret_list
    tfvars[tfvars_key] = conf_secrets

    apps = [config_charm, "neutron"]
    _apply_tfvars(feature, deployment, tfvars, apps)


def _read_switch_config(
    protocol: str,
    name: str,
    configfile: typing.TextIO,
    additional_files: list[tuple[str, typing.TextIO]],
) -> feature_config._Config:
    names = [name for name, _ in additional_files]
    if len(names) != len(set(names)):
        raise click.ClickException("Duplicate additional files.")

    additional_files_dict = {}
    for name, file in additional_files:
        additional_files_dict[name] = file.read()

    config_obj = feature_config._Config(
        configfile=configfile.read(),
        additional_files=additional_files_dict,
    )

    # validate switch config.
    config = {protocol: {name: config_obj}}
    feature_config._SwitchConfigs(**config)

    return config_obj


@click.command()
@pass_method_obj
def switch_config_list(*args, **kwargs):
    """List Neutron baremetal / generic switch configurations."""
    _switch_config_list(*args, **kwargs)


def _switch_config_list(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
) -> None:
    tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
    tfvars = _get_tfvars(feature, deployment)
    items = tfvars.get(tfvars_key, {})

    table = Table(
        Column("Protocol"),
        Column("Name"),
        box=box.SIMPLE,
    )
    for protocol in ["netconf", "generic"]:
        for secret_name in items.get(protocol, []):
            if secret_name.startswith("switch-config-"):
                name = secret_name.removeprefix("switch-config-")
                table.add_row(protocol, name)

    console.print(table)


@click.command()
@click.argument("protocol", type=click.Choice(["netconf", "generic"]))
@click.argument("name")
@click.option(
    "--config",
    required=True,
    type=click.File("r"),
    help="The path to a baremetal / generic switch config file.",
)
@click.option(
    "--additional-file",
    multiple=True,
    type=(str, click.File("r")),
    help="The path to an additional file. Can be repeated for multiple files",
)
@click_option_show_hints
@pass_method_obj
def switch_config_update(*args, **kwargs):
    """Update Neutron baremetal / generic switch configuration."""
    _switch_config_update(*args, **kwargs)


def _switch_config_update(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    protocol: str,
    name: str,
    config: typing.TextIO,
    additional_file: list[tuple[str, typing.TextIO]],
    show_hints: bool,
) -> None:
    secret_name = _switch_config_secret_name(name)
    jhelper = JujuHelper(deployment.juju_controller)
    if not jhelper.secret_exists(OPENSTACK_MODEL, secret_name):
        raise click.ClickException(f"Secret {name} does not exist.")

    config_obj = _read_switch_config(protocol, name, config, additional_file)

    # Update secret.
    secret_data = {
        "conf": config_obj.configfile,
        **config_obj.additional_files,
    }
    jhelper.update_secret(
        OPENSTACK_MODEL,
        secret_name,
        secret_data,
    )

    click.echo(f"Switch config {name} updated.")


@click.command()
@click.argument("name")
@click_option_show_hints
@pass_method_obj
def switch_config_delete(*args, **kwargs):
    """Delete Neutron baremetal / generic switch configuration."""
    _switch_config_delete(*args, **kwargs)


def _switch_config_delete(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    name: str,
    show_hints: bool,
) -> None:
    secret_name = _switch_config_secret_name(name)
    jhelper = JujuHelper(deployment.juju_controller)

    try:
        secret = jhelper.show_secret(OPENSTACK_MODEL, secret_name)
    except JujuSecretNotFound:
        raise click.ClickException(f"Secret {name} does not exist.")

    # Remove secret.
    jhelper.remove_secret(
        OPENSTACK_MODEL,
        secret_name,
    )

    # infer protocol.
    tfvars_key = constants.NEUTRON_SWITCH_CONF_SECRETS_TFVAR
    tfvars = _get_tfvars(feature, deployment)
    conf_secrets = tfvars.get(tfvars_key, {})
    if secret_name in conf_secrets.get("netconf", []):
        protocol = "netconf"
        config_charm = "neutron-baremetal-switch-config"
    else:
        protocol = "generic"
        config_charm = "neutron-generic-switch-config"

    conf_secrets[protocol].remove(secret_name)

    # Update and apply terraform vars.
    tfvars_key = constants.SWITCH_CONFIG_TFVAR[protocol]
    conf_secrets = tfvars.get(tfvars_key, "").split(",")
    secret_id = secret.uri.unique_identifier
    if secret_id in conf_secrets:
        conf_secrets.remove(secret_id)

    tfvars[tfvars_key] = ",".join(conf_secrets)

    _apply_tfvars(
        feature,
        deployment,
        tfvars,
        [config_charm, "neutron"],
        apps_desired_status=["active", "blocked"],
    )


def _baremetal_resource_add(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    tfvars_key: str,
    items: dict[str, dict],
    charm_name_prefix: str,
    replace: bool = False,
    apps_desired_status: list[str] = ["active"],
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
    _apply_tfvars(feature, deployment, tfvars, apps, apps_desired_status)

    click.echo(f"Resource(s) {apps} added.")


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
    apps_desired_status: list[str] = ["active"],
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
        jhelper.wait_until_desired_status(
            OPENSTACK_MODEL,
            apps,
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=status_queue,
            status=apps_desired_status,
        )
    except (JujuWaitException, TimeoutError):
        raise click.ClickException(f"Timed out waiting for {apps} to become active.")
    finally:
        task.stop()


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


def _run_set_temp_url_secret(
    feature: OpenStackControlPlaneFeature,
    deployment: Deployment,
    apps: list[str],
):
    jhelper = JujuHelper(deployment.juju_controller)

    try:
        for app in apps:
            unit = jhelper.get_leader_unit(app, OPENSTACK_MODEL)
            jhelper.run_action(unit, OPENSTACK_MODEL, "set-temp-url-secret")
    except (ActionFailedException, LeaderNotFoundException) as e:
        raise click.ClickException(
            f"Error running the set-temp-url-secret action on {app}.",
        ) from e

    LOG.debug(f"Application monitored for readiness: {apps}")
    status_queue: queue.Queue[str] = queue.Queue()
    task = update_status_background(feature, apps, status_queue)
    try:
        jhelper.wait_until_active(
            OPENSTACK_MODEL,
            apps,
            timeout=constants.IRONIC_APP_TIMEOUT,
            queue=status_queue,
        )
    except (JujuWaitException, TimeoutError) as e:
        raise click.ClickException(
            "Error waiting for applications to become active."
        ) from e
    finally:
        task.stop()
