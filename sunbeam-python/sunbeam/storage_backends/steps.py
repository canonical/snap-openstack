# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend deployment steps with interactive UI."""

import logging
from typing import Any, Dict

from rich.status import Status

from sunbeam.core.common import BaseStep, Result, ResultType
from sunbeam.core.deployment import Deployment
from sunbeam.storage_backends.base import (
    StorageBackendConfig,
    StorageBackendService,
)

LOG = logging.getLogger(__name__)


class ValidateConfigStep(BaseStep):
    """Step to validate storage backend configuration."""

    def __init__(self, config: StorageBackendConfig):
        super().__init__(
            "Validate Configuration", f"Validating {config.name} backend configuration"
        )
        self.config = config

    def run(self, status: Status | None = None) -> Result:
        """Validate the configuration."""
        try:
            self.update_status(status, "validating configuration...")
            # Configuration is already validated by Pydantic
            # Additional validation can be added here if needed
            self.update_status(status, "configuration valid")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Configuration validation failed: {e}")
            return Result(ResultType.FAILED, str(e))


class CheckBackendExistsStep(BaseStep):
    """Step to check if backend already exists."""

    def __init__(self, deployment: Deployment, backend_name: str):
        super().__init__(
            "Check Backend Exists",
            f"Checking if backend '{backend_name}' already exists",
        )
        self.deployment = deployment
        self.backend_name = backend_name

    def run(self, status: Status | None = None) -> Result:
        """Check if backend exists."""
        try:
            self.update_status(status, "checking existing backends...")
            service = StorageBackendService(self.deployment)

            if service.backend_exists(self.backend_name):
                return Result(
                    ResultType.FAILED, f"Backend '{self.backend_name}' already exists"
                )

            self.update_status(status, "backend name available")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Failed to check backend existence: {e}")
            return Result(ResultType.FAILED, str(e))


class ValidateBackendExistsStep(BaseStep):
    """Step to validate that backend exists for removal."""

    def __init__(self, deployment: Deployment, backend_name: str):
        super().__init__(
            "Validate Backend Exists",
            f"Validating that backend '{backend_name}' exists",
        )
        self.deployment = deployment
        self.backend_name = backend_name

    def run(self, status: Status | None = None) -> Result:
        """Validate backend exists."""
        try:
            self.update_status(status, "checking backend existence...")
            service = StorageBackendService(self.deployment)

            if not service.backend_exists(self.backend_name):
                return Result(
                    ResultType.FAILED, f"Backend '{self.backend_name}' not found"
                )

            self.update_status(status, "backend found")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Failed to validate backend existence: {e}")
            return Result(ResultType.FAILED, str(e))


class DeployCharmStep(BaseStep):
    """Step to deploy a storage backend charm."""

    def __init__(
        self,
        deployment: Deployment,
        config: StorageBackendConfig,
        charm_name: str,
        charm_config: Dict[str, Any],
        local_charm_path: str,
    ):
        charm_source = local_charm_path if local_charm_path else charm_name
        super().__init__(
            "Deploy Charm", f"Deploying {charm_source} charm for {config.name}"
        )
        self.deployment = deployment
        self.config = config
        self.charm_name = charm_name
        self.charm_config = charm_config
        self.local_charm_path = local_charm_path

    def run(self, status: Status | None = None) -> Result:
        """Deploy the charm."""
        try:
            charm_source = (
                self.local_charm_path if self.local_charm_path else self.charm_name
            )
            self.update_status(status, f"deploying {charm_source}...")
            service = StorageBackendService(self.deployment)

            # Use trust=True for local charms (files or directories)
            trust = bool(self.local_charm_path)

            service.juju_helper.deploy(
                self.config.name,
                charm_source,  # Use local path if provided, otherwise charm name
                service.model,
                config=self.charm_config,
                trust=trust,
            )

            self.update_status(status, "charm deployed successfully")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Failed to deploy charm {self.charm_name}: {e}")
            return Result(ResultType.FAILED, str(e))


class WaitForReadyStep(BaseStep):
    """Step to wait for application to be ready."""

    def __init__(
        self, deployment: Deployment, config: StorageBackendConfig, timeout: int = 600
    ):
        super().__init__("Wait for Ready", f"Waiting for {config.name} to be ready")
        self.deployment = deployment
        self.config = config
        self.timeout = timeout

    def run(self, status: Status | None = None) -> Result:
        """Wait for application to be ready."""
        try:
            self.update_status(status, "waiting for application to be ready...")
            service = StorageBackendService(self.deployment)

            service.juju_helper.wait_application_ready(
                self.config.name,
                model=service.model,
                timeout=self.timeout,
            )

            self.update_status(status, "application is ready")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Application {self.config.name} failed to become ready: {e}")
            return Result(ResultType.FAILED, str(e))


class IntegrateWithCinderVolumeStep(BaseStep):
    """Step to integrate storage backend with Cinder."""

    def __init__(self, deployment: Deployment, config: StorageBackendConfig):
        super().__init__(
            "Integrate with Cinder", f"Integrating {config.name} with Cinder volume"
        )
        self.deployment = deployment
        self.config = config

    def run(self, status: Status | None = None) -> Result:
        """Integrate with Cinder."""
        try:
            self.update_status(status, "creating integration with Cinder volume...")
            service = StorageBackendService(self.deployment)

            service.juju_helper.integrate(
                service.model, self.config.name, "cinder-volume", "storage-backend"
            )

            self.update_status(status, "integration created successfully")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Failed to integrate {self.config.name} with cinder-volume: {e}")
            return Result(ResultType.FAILED, str(e))


class RemoveBackendStep(BaseStep):
    """Step to remove a storage backend."""

    def __init__(self, deployment: Deployment, backend_name: str):
        super().__init__("Remove Backend", f"Removing backend '{backend_name}'")
        self.deployment = deployment
        self.backend_name = backend_name

    def run(self, status: Status | None = None) -> Result:
        """Remove the backend."""
        try:
            self.update_status(status, "removing application...")
            service = StorageBackendService(self.deployment)

            service.juju_helper.remove_application(
                self.backend_name, model=service.model
            )

            self.update_status(status, "backend removed successfully")
            return Result(ResultType.COMPLETED)
        except Exception as e:
            LOG.error(f"Failed to remove backend {self.backend_name}: {e}")
            return Result(ResultType.FAILED, str(e))
