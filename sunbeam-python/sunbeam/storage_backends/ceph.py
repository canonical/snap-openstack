# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
from typing import Any, Dict

import click
import pydantic
from rich.console import Console

from sunbeam.core.common import BaseStep
from sunbeam.core.deployment import Deployment
from sunbeam.storage_backends.base import (
    BackendValidationException,
    StorageBackendBase,
    StorageBackendConfig,
)
from sunbeam.storage_backends.steps import (
    CheckBackendExistsStep,
    DeployCharmStep,
    IntegrateWithCinderStep,
    RemoveBackendStep,
    ValidateBackendExistsStep,
    ValidateConfigStep,
    WaitForReadyStep,
)

LOG = logging.getLogger(__name__)
console = Console()


class CephConfig(StorageBackendConfig):
    """Configuration model for Ceph storage backend."""

    pool_name: str = pydantic.Field(default="cinder-ceph", description="Ceph pool name")
    rbd_user: str = pydantic.Field(default="cinder", description="RBD user")

    @pydantic.validator("pool_name")
    def validate_pool_name(cls, v):  # noqa: N805
        """Validate pool name field."""
        if not v or not v.strip():
            raise ValueError("Pool name cannot be empty")
        return v.strip()


class CephBackend(StorageBackendBase):
    name = "ceph"
    display_name = "Ceph Storage Backend"

    def get_config_model(self) -> type[StorageBackendConfig]:
        """Return the configuration model for Ceph backend."""
        return CephConfig

    def validate_config(self, config: StorageBackendConfig) -> None:
        """Validate Ceph-specific configuration."""
        if not isinstance(config, CephConfig):
            raise BackendValidationException(
                "Invalid configuration type for Ceph backend"
            )

        # Additional validation can be added here
        if not config.pool_name:
            raise BackendValidationException("Ceph pool name is required")

    def _create_add_plan(
        self, deployment: Deployment, config: CephConfig
    ) -> list[BaseStep]:
        """Create a plan for adding a Ceph storage backend."""
        return [
            ValidateCephConfigStep(config),
            CheckBackendExistsStep(deployment, config.name),
            DeployCephCharmStep(deployment, config),
            WaitForCephReadyStep(deployment, config),
            IntegrateWithCinderStep(deployment, config),
        ]

    def _create_remove_plan(
        self, deployment: Deployment, backend_name: str
    ) -> list[BaseStep]:
        """Create a plan for removing a Ceph storage backend."""
        return [
            ValidateBackendExistsStep(deployment, backend_name),
            RemoveCephBackendStep(deployment, backend_name),
        ]

    def _prompt_for_config(self) -> Dict[str, Any]:
        """Prompt user for Ceph backend configuration."""
        name = click.prompt("Backend name", default="ceph-backend")
        pool_name = click.prompt("Ceph pool name", default="cinder-ceph")
        rbd_user = click.prompt("RBD user", default="cinder")

        return {
            "name": name,
            "pool_name": pool_name,
            "rbd_user": rbd_user,
        }


# Ceph-specific step implementations
class ValidateCephConfigStep(ValidateConfigStep):
    """Step to validate Ceph-specific configuration."""

    def __init__(self, config: CephConfig):
        super().__init__(config)
        self.name = "Validate Ceph Configuration"
        self.description = f"Validating Ceph configuration for {config.name}"


class DeployCephCharmStep(DeployCharmStep):
    """Step to deploy Ceph charm."""

    def __init__(self, deployment: Deployment, config: CephConfig):
        charm_config = {
            "pool-name": config.pool_name,
            "rbd-user": config.rbd_user,
        }
        super().__init__(deployment, config, "cinder-ceph", charm_config)


class WaitForCephReadyStep(WaitForReadyStep):
    """Step to wait for Ceph to be ready."""

    def __init__(self, deployment: Deployment, config: CephConfig):
        super().__init__(deployment, config, timeout=300)
        self.description = f"Waiting for Ceph {config.name} to be ready"


class RemoveCephBackendStep(RemoveBackendStep):
    """Step to remove Ceph backend."""

    def __init__(self, deployment: Deployment, backend_name: str):
        super().__init__(deployment, backend_name)
        self.description = f"Removing Ceph backend '{backend_name}'"
