# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend service layer."""

import logging
from typing import Any, Dict, List

from rich.console import Console

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import read_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper

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
        """List all Terraform-managed storage backends with dynamic status.

        Returns:
            List of StorageBackendInfo objects for all Terraform-managed
            storage backends with real-time status and charm information
        """
        backends = []
        client = self.deployment.get_client()
        jhelper = JujuHelper(self.deployment.juju_controller)

        # Get all available backend types from registry
        from .registry import StorageBackendRegistry

        registry = StorageBackendRegistry()
        available_backends = registry.get_backends()

        # Search each backend type's individual config key
        for backend_type, backend_instance in available_backends.items():
            try:
                # Each backend stores config in its
                # own key: TerraformVarsStorageBackends{Type}
                backend_config_key = backend_instance.tfvar_config_key
                LOG.debug(
                    f"Searching for {backend_type} backends in config key: "
                    f"{backend_config_key}"
                )

                current_config = read_config(client, backend_config_key)

                # Look for backend-specific keys (e.g., "hitachi_backends",
                # "purestorage_backends")
                backend_key = f"{backend_type}_backends"

                if backend_key in current_config:
                    for backend_name, backend_config in current_config[
                        backend_key
                    ].items():
                        try:
                            # Get actual application name from Terraform config
                            app_name = backend_config.get(
                                "application_name", backend_name
                            )

                            # Query actual status and charm from Juju
                            status = self._get_application_status(jhelper, app_name)
                            charm_name = self._get_application_charm(jhelper, app_name)

                            backend = StorageBackendInfo(
                                name=backend_name,
                                backend_type=backend_type,
                                status=status,
                                charm=charm_name,
                                config=backend_config.get("charm_config", {}),
                            )
                            backends.append(backend)
                            LOG.debug(f"Found {backend_type} backend: {backend_name}")
                        except Exception as e:
                            LOG.warning(
                                f"Error processing {backend_type}"
                                f"backend {backend_name}: {e}"
                            )
                            continue
                else:
                    LOG.debug(f"No {backend_key} found in config for {backend_type}")

            except ConfigItemNotFoundException:
                LOG.debug(f"No configuration found for {backend_type} backends")
                continue
            except Exception as e:
                LOG.warning(f"Error reading {backend_type} backends from clusterd: {e}")
                continue

        return backends

    def _get_application_status(self, jhelper: JujuHelper, app_name: str) -> str:
        """Get application status from Juju.

        Args:
            jhelper: JujuHelper instance for Juju operations
            app_name: Name of the Juju application

        Returns:
            Application status string or "unknown" if not found
        """
        try:
            # Get model status using JujuHelper.get_model_status()
            model_status = jhelper.get_model_status(
                self.deployment.openstack_machines_model
            )

            # Check if application exists in the model
            if app_name in model_status.apps:
                app_status = model_status.apps[app_name]
                return app_status.app_status.current

            return "not-found"
        except Exception as e:
            LOG.debug(f"Failed to get status for application {app_name}: {e}")
            return "unknown"

    def _get_application_charm(self, jhelper: JujuHelper, app_name: str) -> str:
        """Get charm name from Juju.

        Args:
            jhelper: JujuHelper instance for Juju operations
            app_name: Name of the Juju application

        Returns:
            Charm name or fallback name if not found
        """
        try:
            # Get model status using JujuHelper.get_model_status()
            model_status = jhelper.get_model_status(
                self.deployment.openstack_machines_model
            )

            # Check if application exists in the model
            if app_name in model_status.apps:
                app_status = model_status.apps[app_name]
                charm_url = app_status.charm
                return charm_url

            return "Not Found"

        except Exception as e:
            LOG.debug(f"Failed to get charm for application {app_name}: {e}")
            return "Unknown"

    def _load_backend_tfvars(self, backend_type: str) -> Dict[str, Any]:
        """Safely load storage backend Terraform variables from clusterd.

        Args:
            backend_type: The backend type (e.g., 'hitachi', 'purestorage')

        Returns:
            Dict containing the backend configuration, empty dict if not found.
        """
        try:
            # Get the backend instance to access its config key
            from .registry import StorageBackendRegistry

            registry = StorageBackendRegistry()
            available_backends = registry.get_backends()

            if backend_type not in available_backends:
                return {}

            backend_instance = available_backends[backend_type]
            backend_config_key = backend_instance.tfvar_config_key

            client = self.deployment.get_client()
            return read_config(client, backend_config_key)
        except ConfigItemNotFoundException:
            return {}
        except Exception:
            return {}

    def _iter_backend_items(self, tfvars: Dict[str, Any], backend_type: str):
        """Yield (name, config) pairs for a given backend_type."""
        typed_key = f"{backend_type}_backends"
        typed_map = tfvars.get(typed_key, {}) or {}
        if isinstance(typed_map, dict):
            for name, cfg in typed_map.items():
                yield name, cfg

    def _get_backend_entry(
        self, backend_type: str, backend_name: str
    ) -> Dict[str, Any] | None:
        """Return the backend config entry if found, else None."""
        tfvars = self._load_backend_tfvars(backend_type)
        for name, cfg in self._iter_backend_items(tfvars, backend_type):
            if name == backend_name:
                return cfg
        return None

    def backend_exists(self, backend_name: str, backend_type: str) -> bool:
        """Check if a backend exists in Terraform configuration."""
        return self._get_backend_entry(backend_type, backend_name) is not None

    def get_backend_config(
        self, backend_name: str, backend_type: str
    ) -> Dict[str, Any]:
        """Get the current configuration of a storage backend."""
        try:
            entry = self._get_backend_entry(backend_type, backend_name)
            if not entry:
                raise BackendNotFoundException(f"Backend '{backend_name}' not found")

            # Return the full backend configuration, not just charm_config
            # This includes credentials that can be masked by the display logic
            full_config = dict(entry)

            # Merge charm_config into the top level for backward compatibility
            charm_config = entry.get("charm_config", {})
            full_config.update(charm_config)

            # Remove the charm_config key to avoid showing it as a separate field
            full_config.pop("charm_config", None)

            return full_config

        except BackendNotFoundException:
            raise
        except Exception as e:
            LOG.error(f"Failed to get config for backend '{backend_name}': {e}")
            raise StorageBackendException(f"Failed to get backend config: {e}") from e
