# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging

import click
from rich.console import Console

from sunbeam.core.deployment import Deployment
from sunbeam.storage.registry import StorageBackendRegistry

LOG = logging.getLogger(__name__)
console = Console()

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group("storage", context_settings=CONTEXT_SETTINGS)
@click.pass_context
def storage(ctx):
    """Manage Cinder storage backends.

    Provides commands to add, remove, configure and list storage backends.
    Supports multiple backend types including Hitachi VSP and others.
    """
    # Ensure we have a deployment object
    if not hasattr(ctx, "obj") or not isinstance(ctx.obj, Deployment):
        raise click.ClickException(
            "Storage commands require a valid deployment context. "
            "Please ensure sunbeam is properly initialized."
        )


def register_storage_commands(deployment: Deployment) -> None:
    """Register storage backend commands with the storage group.

    This function is called from main.py to register all storage backend
    commands dynamically based on available backends.
    """
    try:
        StorageBackendRegistry().register_cli_commands(storage, deployment)
        LOG.debug("Storage backend commands registered successfully")
    except Exception as e:
        LOG.error(f"Failed to register storage backend commands: {e}")
        # Don't raise here as we want the CLI to still work
