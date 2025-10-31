# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend service layer."""

import logging

from rich.console import Console

from sunbeam.clusterd.models import StorageBackend
from sunbeam.clusterd.service import (
    StorageBackendNotFoundException,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.storage.models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    StorageBackendInfo,
)

LOG = logging.getLogger(__name__)
console = Console()


class StorageBackendService:
    """Service layer for storage backend operations."""

    def __init__(self, deployment: Deployment, jhelper: JujuHelper):
        self.deployment = deployment
        self.jhelper = jhelper
        self.model = jhelper.get_model_name_with_owner(
            self.deployment.openstack_machines_model
        )
        # Use a consistent config key for all storage backends
        self._tfvar_config_key = "TerraformVarsStorageBackends"

    def list_backends(self) -> list[StorageBackendInfo]:
        """List all Terraform-managed storage backends with dynamic status.

        Returns:
            List of StorageBackendInfo objects for all Terraform-managed
            storage backends with real-time status and charm information
        """
        backends = []
        client = self.deployment.get_client()

        enabled_backends = client.cluster.get_storage_backends()
        # Search each backend type's individual config key
        for backend in enabled_backends.root:
            try:
                # Get actual application name from Terraform config
                app_name = backend.name

                # Query actual status and charm from Juju
                status = self._get_application_status(self.jhelper, app_name)
                charm_name = self._get_application_charm(self.jhelper, app_name)

                backend_info = StorageBackendInfo(
                    name=backend.name,
                    backend_type=backend.type,
                    status=status,
                    charm=charm_name,
                    config=backend.config,
                )
                backends.append(backend_info)
                LOG.debug(f"Found {backend.type} backend: {backend.name}")
            except Exception as e:
                LOG.warning(
                    f"Error processing {backend.type} backend {backend.name}: {e}"
                )
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

    def backend_exists(self, backend_name: str, backend_type: str) -> bool:
        """Check if a backend exists in Terraform configuration."""
        client = self.deployment.get_client()
        try:
            backend = client.cluster.get_storage_backend(backend_name)
            if backend.type != backend_type:
                raise BackendAlreadyExistsException("Backend type mismatch.")
            return True
        except StorageBackendNotFoundException:
            return False

    def get_backend(self, backend_name: str) -> StorageBackend:
        """Get a specific storage backend by name."""
        client = self.deployment.get_client()
        try:
            return client.cluster.get_storage_backend(backend_name)
        except StorageBackendNotFoundException as e:
            LOG.debug(f"Storage backend not found: {backend_name}", exc_info=True)
            raise BackendNotFoundException() from e
