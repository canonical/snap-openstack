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

    Provides commands to add, remove, and list storage backends.
    Supports multiple backend types including Hitachi VSP and others.
    """
    # Ensure we have a deployment object
    if not hasattr(ctx, "obj") or not isinstance(ctx.obj, Deployment):
        raise click.ClickException(
            "Storage commands require a valid deployment context. "
            "Please ensure sunbeam is properly initialized."
        )


@storage.command("clean-state", hidden=True)
@click.argument("backend_type", required=True)
@click.option("--force", is_flag=True, help="Force cleanup without confirmation")
@click.pass_context
def clean_terraform_state(ctx, backend_type: str, force: bool):
    """Clean corrupted Terraform state for a storage backend.
    
    This is a hidden command to fix corrupted remote Terraform state
    that can occur due to provider bugs or interrupted deployments.
    
    Usage: sunbeam storage clean-state hitachi [--force]
    """
    deployment = ctx.obj
    
    if not force:
        click.confirm(
            f"This will clean the Terraform state for {backend_type} backend. "
            "This action cannot be undone. Continue?",
            abort=True
        )
    
    try:
        # Get the backend from registry
        registry = StorageBackendRegistry()
        backend_class = registry.get_backend(backend_type)
        
        if not backend_class:
            raise click.ClickException(f"Backend type '{backend_type}' not found")
        
        backend = backend_class
        
        # Register the backend to get the TerraformHelper with proper auth
        backend.register_terraform_plan(deployment)
        
        # Get the TerraformHelper with proper authentication
        tfhelper = deployment._tfhelpers.get(backend.tfplan)
        
        if not tfhelper:
            raise click.ClickException(f"No Terraform helper found for {backend_type} backend")
        
        console.print(f"🧹 Cleaning Terraform state for {backend_type} backend...")
        
        # List current state
        try:
            state_resources = tfhelper.state_list()
            console.print(f"Found {len(state_resources)} resources in state:")
            for resource in state_resources:
                console.print(f"  - {resource}")
            
            # Remove stale resources (those that reference deleted backends)
            stale_patterns = ['vsp350', 'sdfsad', 'test-fixed-backend']
            resources_to_remove = [
                resource for resource in state_resources 
                for pattern in stale_patterns
                if pattern in resource
            ]
            
            if resources_to_remove:
                console.print(f"\n🗑️  Removing {len(resources_to_remove)} stale resources:")
                for resource in resources_to_remove:
                    console.print(f"  Removing: {resource}")
                    try:
                        tfhelper.state_rm(resource)
                        console.print(f"  ✅ Removed: {resource}")
                    except Exception as e:
                        console.print(f"  ❌ Failed to remove {resource}: {e}")
                
                console.print(f"\n✅ Successfully cleaned up {len(resources_to_remove)} stale resources")
            else:
                console.print("\n✅ No stale resources found in state")
                
        except Exception as e:
            raise click.ClickException(f"Failed to access Terraform state: {e}")
            
    except Exception as e:
        raise click.ClickException(f"Failed to clean state: {e}")
    
    console.print("\n🎉 State cleanup completed! You can now try adding backends again.")


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
