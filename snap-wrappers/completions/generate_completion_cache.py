# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Helper script to generate shell completion cache for Sunbeam CLI.

Run during Snapcraft's packing to generate a single completion cache file.

Usage: ./bin/python3 completions/generate_completion_cache.py <cache_file>
"""

import os
import sys

from sunbeam.commands import configure as configure_cmds
from sunbeam.commands import dashboard as dashboard_cmds
from sunbeam.commands import dashboard_url as dashboard_url_cmds
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
from sunbeam.feature_manager import list_feature_gates, list_features
from sunbeam.main import (
    cli,
    disable,
    enable,
    identity_group,
    juju,
    manifest,
    provider_group,
    proxy,
    utils,
)

try:
    from sunbeam.provider.commands import deployment_group
    from sunbeam.provider.local.commands import LocalProvider
except ImportError as e:
    LocalProvider = None
    print(f"Warning: Could not import provider commands: {e}", file=sys.stderr)

try:
    from sunbeam.storage.manager import storage as storage_group
except ImportError as e:
    storage_group = None
    print(f"Warning: Could not import storage commands: {e}", file=sys.stderr)


def build_completion(group, entries, prefix="sunbeam"):
    """Traverse the Click CLI command tree and collect completions at each level.

    Args:
        group: Click group or command to traverse.
        entries: dict to populate with {key: [completion_lines]}.
        prefix: current command prefix used as cache key.
    """
    completions = []
    commands = getattr(group, "commands", {})
    if not commands and hasattr(group, "list_commands"):
        try:
            ctx = group.make_context(
                prefix.split("_")[-1], [], parent=None, resilient_parsing=True
            )
            for name in group.list_commands(ctx):
                cmd = group.get_command(ctx, name)
                if cmd:
                    commands[name] = cmd
        except Exception as e:
            print(
                f"Warning: Could not list commands for {prefix}: {e}",
                file=sys.stderr,
            )

    for name, cmd in sorted(commands.items()):
        completions.append(f"plain,{name}")

    if completions:
        entries[prefix] = completions

    # Recurse into subgroups
    for name, cmd in sorted(commands.items()):
        if hasattr(cmd, "commands") or hasattr(cmd, "list_commands"):
            build_completion(cmd, entries, f"{prefix}_{name}")


def register_commands():
    """Register all CLI commands for completion cache generation."""
    # Static commands
    cli.add_command(prepare_node_cmds.prepare_node_script)
    cli.add_command(configure_cmds.configure)
    cli.add_command(generate_cloud_config_cmds.cloud_config)
    cli.add_command(launch_cmds.launch)
    cli.add_command(openrc_cmds.openrc)
    cli.add_command(dashboard_url_cmds.dashboard_url)
    cli.add_command(dashboard_cmds.dashboard)
    cli.add_command(identity_group)
    identity_group.add_command(provider_group)
    identity_group.add_command(sso_cmd.set_saml_x509)
    provider_group.add_command(sso_cmd.list_sso)
    provider_group.add_command(sso_cmd.add_sso)
    provider_group.add_command(sso_cmd.remove_sso)
    provider_group.add_command(sso_cmd.update_sso)
    provider_group.add_command(sso_cmd.get_openid_redirect_uri)
    provider_group.add_command(sso_cmd.purge_sso)
    cli.add_command(manifest)
    manifest.add_command(manifest_cmds.list_manifests)
    manifest.add_command(manifest_cmds.show)
    manifest.add_command(manifest_cmds.generate)
    cli.add_command(proxy)
    proxy.add_command(proxy_cmds.show)
    proxy.add_command(proxy_cmds.set)
    proxy.add_command(proxy_cmds.clear)
    cli.add_command(enable)
    cli.add_command(disable)
    cli.add_command(plans_cmd.plans)
    cli.add_command(list_features)
    cli.add_command(list_feature_gates)
    cli.add_command(utils)
    utils.add_command(utils_cmds.juju_login)
    cli.add_command(juju)
    juju.add_command(juju_cmds.register_controller)
    juju.add_command(juju_cmds.unregister_controller)

    # LocalProvider commands
    if LocalProvider:
        try:
            LocalProvider().register_cli(
                cli, configure_cmds.configure, deployment_group
            )
            cli.add_command(deployment_group)
        except Exception as e:
            print(
                f"Warning: Could not register provider commands: {e}",
                file=sys.stderr,
            )

    # Storage group
    if storage_group:
        cli.add_command(storage_group)


def main():
    """Main entry point for cache generation."""
    # Parse CLI arguments
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(f"Usage: {sys.argv[0]} <cache_file>", file=sys.stderr)
        sys.exit(1)

    cache_file = sys.argv[1]

    register_commands()

    entries = {}
    build_completion(cli, entries)

    # Write the cache file
    cache_dir = os.path.dirname(cache_file)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)

    with open(cache_file, "w", encoding="utf-8") as f:
        for key in sorted(entries.keys()):
            f.write(f"## {key}\n")
            for line in entries[key]:
                f.write(f"{line}\n")

    print(f"Cached {len(entries)} completion entries in {cache_file}")


if __name__ == "__main__":
    main()
