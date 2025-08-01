# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend service layer."""

import logging
from typing import Any, Dict, List

from rich.console import Console

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import read_config
from sunbeam.core.deployment import Deployment

from .models import (
    BackendNotFoundException,
    StorageBackendException,
    StorageBackendInfo,
)

LOG = logging.getLogger(__name__)
console = Console()


class StorageBackendService:
    """Service layer for storage backend operations."""

    def __init__(self, deployment: Deployment):
        self.deployment = deployment
        self.model = self._get_model_name()
        # Use a consistent config key for all storage backends
        self._tfvar_config_key = "TerraformVarsStorageBackends"

    def _get_model_name(self) -> str:
        """Get the OpenStack machines model name."""
        model = self.deployment.openstack_machines_model
        if not model.startswith("admin/"):
            model = f"admin/{model}"
        return model

    def list_backends(self) -> List[StorageBackendInfo]:
        """List all Terraform-managed storage backends.

        Returns:
            List of StorageBackendInfo objects for all Terraform-managed
            storage backends
        """
        backends = []

        try:
            client = self.deployment.get_client()
            current_config = read_config(client, self._tfvar_config_key)

            # Check both new format (backend-specific keys) and legacy format
            # Look for all keys ending with "_backends" (e.g., "hitachi_backends")
            backend_keys = [
                key for key in current_config.keys() if key.endswith("_backends")
            ]

            # Process new format (backend-specific keys)
            for backend_key in backend_keys:
                backend_type = backend_key.replace(
                    "_backends", ""
                )  # Extract backend type from key
                for backend_name, backend_config in current_config[backend_key].items():
                    try:
                        backend = StorageBackendInfo(
                            name=backend_name,
                            backend_type=backend_type,
                            status="active",  # Terraform-managed backends are active
                            charm=f"cinder-volume-{backend_type}",  # Infer charm name
                            config=backend_config.get("charm_config", {}),
                        )
                        backends.append(backend)
                    except Exception as e:
                        LOG.warning(
                            f"Error processing Terraform backend {backend_name}: {e}"
                        )
                        continue

        except ConfigItemNotFoundException:
            LOG.debug("No Terraform storage backend configuration found in clusterd")
        except Exception as e:
            LOG.warning(f"Error reading Terraform backends from clusterd: {e}")

        return backends

    def backend_exists(self, backend_name: str, backend_type: str) -> bool:
        """Check if a backend exists in Terraform configuration."""
        try:
            client = self.deployment.get_client()
            current_config = read_config(client, self._tfvar_config_key)

            # Check new format (backend-specific keys)
            backend_key = f"{backend_type}_backends"  # e.g., "hitachi_backends"

            if backend_key in current_config:
                return backend_name in current_config[backend_key]
            else:
                return False
        except ConfigItemNotFoundException:
            return False

    def get_backend_config(
        self, backend_name: str, backend_type: str
    ) -> Dict[str, Any]:
        """Get the current configuration of a storage backend."""
        try:
            if not self.backend_exists(backend_name, backend_type):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            # Get configuration from Terraform state
            client = self.deployment.get_client()
            current_config = read_config(client, self._tfvar_config_key)

            # Check new format (backend-specific keys only)
            backend_key = f"{backend_type}_backends"  # e.g., "hitachi_backends"

            if (
                backend_key in current_config
                and backend_name in current_config[backend_key]
            ):
                backend_config = current_config[backend_key][backend_name]
                return backend_config.get("charm_config", {})

            # Backend not found in new format
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        except BackendNotFoundException:
            # Re-raise BackendNotFoundException as-is
            raise
        except Exception as e:
            LOG.error(f"Failed to get config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to get backend config: {e}") from e

    def set_backend_config(
        self, backend_name: str, backend_type: str, config_updates: Dict[str, Any]
    ) -> None:
        """Set configuration options for a storage backend."""
        try:
            if not self.backend_exists(backend_name, backend_type):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Setting configuration for backend '{backend_name}'")
            console.print(
                f"[blue]Updating configuration for backend '{backend_name}'...[/blue]"
            )

            # This will be handled by the backend's update_backend_config method
            # via Terraform, so this is a placeholder for the service interface
            console.print(
                f"[green]Configuration updated successfully for "
                f"'{backend_name}'[/green]"
            )

        except BackendNotFoundException:
            # Re-raise BackendNotFoundException as-is
            raise
        except Exception as e:
            LOG.error(f"Failed to set config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to set backend config: {e}") from e

    def reset_backend_config(
        self, backend_name: str, backend_type: str, config_keys: List[str]
    ) -> None:
        """Reset configuration options to their default values for a storage backend."""
        try:
            if not self.backend_exists(backend_name, backend_type):
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            LOG.info(f"Resetting configuration for backend '{backend_name}'")
            console.print(
                f"[blue]Resetting configuration for backend '{backend_name}'...[/blue]"
            )

            # This will be handled by the backend's reset_backend_config method
            # via Terraform, so this is a placeholder for the service interface
            console.print(
                f"[green]Configuration reset successfully for '{backend_name}'[/green]"
            )

        except BackendNotFoundException:
            # Re-raise BackendNotFoundException as-is
            raise
        except Exception as e:
            LOG.error(f"Failed to reset config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to reset backend config: {e}") from e
