# SPDX-FileCopyrightText: 2023 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import sys
from pathlib import Path

import click
from snaphelpers import Snap

from sunbeam import log
from sunbeam.commands import configure as configure_cmds
from sunbeam.commands import dashboard_url as dasboard_url_cmds
from sunbeam.commands import generate_cloud_config as generate_cloud_config_cmds
from sunbeam.commands import juju_utils as juju_cmds
from sunbeam.commands import launch as launch_cmds
from sunbeam.commands import manifest as manifest_cmds
from sunbeam.commands import openrc as openrc_cmds
from sunbeam.commands import plans as plans_cmd
from sunbeam.commands import prepare_node as prepare_node_cmds
from sunbeam.commands import proxy as proxy_cmds
from sunbeam.commands import sso as sso_cmd
from sunbeam.commands import utils as utils_cmds
from sunbeam.core import deployments as deployments_jobs
from sunbeam.provider import commands as provider_cmds
from sunbeam.utils import CatchGroup, clean_env

LOG = logging.getLogger()

# Update the help options to allow -h in addition to --help for
# triggering the help for various commands
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("init", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.option("--quiet", "-q", default=False, is_flag=True)
@click.option("--verbose", "-v", default=False, is_flag=True)
@click.pass_context
def cli(ctx, quiet, verbose):
    """Sunbeam is a small lightweight OpenStack distribution.

    To get started with a single node, all-in-one OpenStack installation, start
    with by initializing the local node. Once the local node has been initialized,
    run the bootstrap process to get a live cloud.
    """


@click.group("identity", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def identity_group(ctx):
    """Manage identity settings."""
    pass


@identity_group.group("provider")
@click.pass_context
def provider_group(ctx):
    """Manage identity providers."""
    pass


@click.group("manifest", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def manifest(ctx):
    """Manage manifests (read-only commands)."""


@click.group("proxy", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def proxy(ctx):
    """Manage proxy configuration."""


@click.group("enable", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.option(
    "-m",
    "--manifest",
    help="Manifest file.",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.pass_context
def enable(ctx, manifest: Path | None = None):
    """Enable features."""


@click.group("disable", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def disable(ctx):
    """Disable features."""


@click.group("utils", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def utils(ctx):
    """Utilities for debugging and managing sunbeam."""


@click.group("juju", context_settings=CONTEXT_SETTINGS, cls=CatchGroup)
@click.pass_context
def juju(ctx):
    """Utilities for managing juju."""


def main():
    clean_env()
    snap = Snap()
    logfile = log.prepare_logfile(snap.paths.user_common / "logs", "sunbeam")
    log.setup_root_logging(logfile)
    LOG.debug("command: %s", " ".join(sys.argv))
    cli.add_command(prepare_node_cmds.prepare_node_script)
    cli.add_command(configure_cmds.configure)
    cli.add_command(generate_cloud_config_cmds.cloud_config)
    cli.add_command(launch_cmds.launch)
    cli.add_command(openrc_cmds.openrc)
    cli.add_command(dasboard_url_cmds.dashboard_url)

    # Add identity group
    cli.add_command(identity_group)

    # Add identity group and commands
    identity_group.add_command(provider_group)
    identity_group.add_command(sso_cmd.set_saml_x509)

    # Add provider commands
    provider_group.add_command(sso_cmd.list_sso)
    provider_group.add_command(sso_cmd.add_sso)
    provider_group.add_command(sso_cmd.remove_sso)
    provider_group.add_command(sso_cmd.update_sso)
    provider_group.add_command(sso_cmd.get_openid_redirect_uri)
    provider_group.add_command(sso_cmd.purge_sso)

    # Cluster management
    provider_cmds.register_providers()
    deployment = provider_cmds.load_deployment(
        snap.paths.real_home / deployments_jobs.DEPLOYMENTS_CONFIG
    )
    provider_cmds.register_cli(cli, configure_cmds.configure, deployment)

    # Manifest management
    cli.add_command(manifest)
    manifest.add_command(manifest_cmds.list_manifests)
    manifest.add_command(manifest_cmds.show)
    manifest.add_command(manifest_cmds.generate)

    # Proxy management
    cli.add_command(proxy)
    proxy.add_command(proxy_cmds.show)
    proxy.add_command(proxy_cmds.set)
    proxy.add_command(proxy_cmds.clear)

    cli.add_command(enable)
    cli.add_command(disable)

    cli.add_command(plans_cmd.plans)

    cli.add_command(utils)
    utils.add_command(utils_cmds.juju_login)

    cli.add_command(juju)
    juju.add_command(juju_cmds.register_controller)
    juju.add_command(juju_cmds.unregister_controller)

    # Register storage backend commands
    deployment.get_storage_manager().register(cli, deployment)

    # Register the features after all groups,commands are registered
    deployment.get_feature_manager().register(cli, deployment)

    cli(obj=deployment)


if __name__ == "__main__":
    main()
