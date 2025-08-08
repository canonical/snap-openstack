# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend base class with integrated Terraform functionality."""

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from packaging.version import Version
from rich.console import Console

from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import BaseStep, read_config, run_plan
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper, TerraformInitStep
from sunbeam.features.interface.v1.base import BaseRegisterable
from sunbeam.storage.steps import (
    BaseStorageBackendDeployStep,
    BaseStorageBackendDestroyStep,
)

from .models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    StorageBackendConfig,
)
from .service import StorageBackendService


class ConcreteStorageBackendDeployStep(BaseStorageBackendDeployStep):
    """Concrete implementation of BaseStorageBackendDeployStep."""

    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables from the backend instance."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, self.backend_config, self.model
        )


class ConcreteStorageBackendDestroyStep(BaseStorageBackendDestroyStep):
    """Concrete implementation of BaseStorageBackendDestroyStep."""

    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables from the backend instance."""
        return self.backend_instance.get_terraform_variables(
            self.backend_name, StorageBackendConfig(name=self.backend_name), self.model
        )


LOG = logging.getLogger(__name__)
console = Console()

# Juju application name validation pattern
# Based on Juju's naming rules: must start with letter, contain only
# letters, numbers, hyphens. Cannot end with hyphen, cannot have
# consecutive hyphens, cannot have numbers after final hyphen
JUJU_APP_NAME_PATTERN = re.compile(r"^[a-z]([a-z0-9]*(-[a-z0-9]*)*)?$")


def validate_juju_application_name(name: str) -> bool:
    """Validate that a name is a valid Juju application name.

    Args:
        name: The application name to validate

    Returns:
        True if valid, False otherwise
    """
    if not name:
        return False

    # Check basic pattern
    if not JUJU_APP_NAME_PATTERN.match(name):
        return False

    # Additional checks for edge cases
    if name.endswith("-"):
        return False

    if "--" in name:
        return False

    # Check that numbers don't appear after the final hyphen
    if "-" in name:
        parts = name.split("-")
        last_part = parts[-1]
        if any(char.isdigit() for char in last_part):
            return False

    return True


class StorageBackendBase(BaseRegisterable, ABC):
    """Base class for storage backends with integrated Terraform functionality."""

    name: str = "base"
    display_name: str = "Base Storage Backend"
    version = Version("0.0.1")
    tf_plan_location = "FEATURE_REPO"  # Plans stored in feature directory
    user_manifest = None  # Path to user manifest file

    def __init__(self):
        """Initialize storage backend."""
        super().__init__()
        self.tfplan = "storage-backend-plan"
        self.tfplan_dir = "deploy-storage-backend"
        self._manifest: Optional[Manifest] = None
        self.service: Optional[StorageBackendService] = None

    def _get_service(self, deployment: Deployment) -> StorageBackendService:
        """Get or create the storage backend service."""
        if self.service is None:
            self.service = StorageBackendService(deployment)
        return self.service

    # Terraform-related properties and methods
    @property
    def manifest(self) -> Manifest:
        """Return the manifest."""
        if self._manifest:
            return self._manifest

        manifest = click.get_current_context().obj.get_manifest(self.user_manifest)
        self._manifest = manifest
        if self._manifest is None:
            raise ValueError("Failed to load manifest")
        return self._manifest

    @property
    def tfvar_config_key(self) -> str:
        """Config key for storing Terraform variables in clusterd."""
        return "TerraformVarsStorageBackends"  # Use shared config key for all backends

    # Abstract methods that each backend must implement
    @abstractmethod
    def create_deploy_step(
        self,
        deployment: Deployment,
        client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        backend_config: StorageBackendConfig,
        model: str,
    ) -> BaseStep:
        """Create a deployment step for this backend."""
        pass

    @abstractmethod
    def create_destroy_step(
        self,
        deployment: Deployment,
        client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        model: str,
    ) -> BaseStep:
        """Create a destruction step for this backend."""
        pass

    @abstractmethod
    def create_update_config_step(
        self,
        deployment: Deployment,
        backend_name: str,
        config_updates: Dict[str, Any],
    ) -> BaseStep:
        """Create a configuration update step for this backend."""
        pass

    def register_terraform_plan(self, deployment: Deployment) -> None:
        """Register storage backend Terraform plan with deployment system."""
        import shutil

        from sunbeam.core.terraform import TerraformHelper

        # Get the plan source path
        backend_self_contained = (
            Path(__file__).parent / "backends" / self.name / self.tfplan_dir
        )

        if backend_self_contained.exists():
            plan_source = backend_self_contained
        else:
            raise FileNotFoundError(
                f"Terraform plan not found at {backend_self_contained}"
            )

        # Copy plan to deployment's plans directory
        dst = deployment.plans_directory / self.tfplan_dir
        shutil.copytree(plan_source, dst, dirs_exist_ok=True)

        # Create TerraformHelper
        env = {}
        env.update(deployment._get_juju_clusterd_env())
        env.update(deployment.get_proxy_settings())

        tfhelper = TerraformHelper(
            path=dst,
            plan=self.tfplan,
            tfvar_map={},
            backend="http",
            env=env,
            clusterd_address=deployment.get_clusterd_http_address(),
        )

        # Register the helper with the deployment's tfhelpers
        deployment._tfhelpers[self.tfplan] = tfhelper

    def backend_exists(self, deployment: Deployment, backend_name: str) -> bool:
        """Check if a backend exists by reading Terraform state."""
        try:
            client = deployment.get_client()
            current_config = read_config(client, self.tfvar_config_key)

            # Check new format (backend-specific keys only)
            backend_key = f"{self.name}_backends"  # e.g., "hitachi_backends"

            if backend_key in current_config:
                return backend_name in current_config[backend_key]
            else:
                return False

        except ConfigItemNotFoundException:
            return False

    def add_backend(
        self,
        deployment: Deployment,
        backend_name: str,
        config: StorageBackendConfig,
        console: Console,
    ) -> None:
        """Add a storage backend using Terraform deployment."""
        # Validate backend name follows Juju application naming rules
        if not validate_juju_application_name(backend_name):
            raise click.ClickException(
                f"Invalid backend name '{backend_name}'. "
                f"Backend names must be valid Juju application names: "
                f"start with a letter, contain only lowercase letters, numbers,"
                f"and hyphens, cannot end with hyphen, cannot"
                f"have consecutive hyphens, and cannot have numbers"
                f"after the final hyphen."
            )

        if self.backend_exists(deployment, backend_name):
            raise BackendAlreadyExistsException(
                f"Backend '{backend_name}' already exists"
            )

        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        plan = [
            TerraformInitStep(tfhelper),
            self.create_deploy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                config,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def remove_backend(
        self, deployment: Deployment, backend_name: str, console: Console
    ) -> None:
        """Remove a storage backend using Terraform."""
        if not self.backend_exists(deployment, backend_name):
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        # Register our Terraform plan with the deployment system
        self.register_terraform_plan(deployment)

        # Get standard Sunbeam helpers
        client = deployment.get_client()
        tfhelper = deployment.get_tfhelper(self.tfplan)
        jhelper = JujuHelper(deployment.juju_controller)

        # Create removal plan - each backend should implement its own destroy step
        plan = [
            TerraformInitStep(tfhelper),
            self.create_destroy_step(
                deployment,
                client,
                tfhelper,
                jhelper,
                self.manifest,
                backend_name,
                deployment.openstack_machines_model,
            ),
        ]

        run_plan(plan, console)

    def update_backend_config(
        self, deployment: Deployment, backend_name: str, config_updates: Dict[str, Any]
    ) -> None:
        """Update backend configuration using Terraform."""
        if not self.backend_exists(deployment, backend_name):
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            self.create_update_config_step(deployment, backend_name, config_updates),
        ]

        run_plan(plan, console)

    def reset_backend_config(
        self, deployment: Deployment, backend_name: str, config_keys: List[str]
    ) -> None:
        """Reset backend configuration using Terraform."""
        if not self.backend_exists(deployment, backend_name):
            raise BackendNotFoundException(f"Backend '{backend_name}' not found")

        # For reset, we pass empty config_updates and let the backend handle reset logic
        plan = [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            self.create_update_config_step(
                deployment, backend_name, {"_reset_keys": config_keys}
            ),
        ]

        run_plan(plan, console)

    def _get_backend_type(self, charm_name: str) -> str:
        """Determine backend type from charm name.

        Args:
            charm_name: The charm name (e.g., 'cinder-volume-hitachi')

        Returns:
            Backend type string
        """
        if "hitachi" in charm_name:
            return "hitachi"
        elif "ceph" in charm_name:
            return "ceph"
        else:
            return "unknown"

    @property
    def config_class(self) -> type[StorageBackendConfig]:
        """Return the configuration class for this backend."""
        return StorageBackendConfig

    def _prompt_for_config(self, backend_name: str) -> Any:
        """Prompt user for backend configuration.

        Calls backend-specific implementation.
        """
        return self.prompt_for_config(backend_name)

    def _create_add_plan(
        self, deployment: Deployment, config: Any, local_charm: str = ""
    ) -> List[BaseStep]:
        """Create a plan for adding a storage backend. Override in subclasses."""
        return [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            ConcreteStorageBackendDeployStep(
                deployment,
                deployment.get_client(),
                deployment.get_tfhelper(self.tfplan),
                JujuHelper(deployment.juju_controller),
                deployment.get_manifest(),
                config.name,
                config,
                self,
                "openstack",
            ),
        ]

    def _create_remove_plan(
        self, deployment: Deployment, backend_name: str
    ) -> List[BaseStep]:
        """Create a plan for removing a storage backend. Override in subclasses."""
        return [
            TerraformInitStep(deployment.get_tfhelper(self.tfplan)),
            ConcreteStorageBackendDestroyStep(
                deployment,
                deployment.get_client(),
                deployment.get_tfhelper(self.tfplan),
                JujuHelper(deployment.juju_controller),
                deployment.get_manifest(),
                backend_name,
                self,
                "openstack",
            ),
        ]

    # Backend-specific properties that subclasses should override
    @property
    def backend_type(self) -> str:
        """Backend type identifier. Override in subclasses."""
        return self.name

    @property
    def charm_name(self) -> str:
        """Charm name for this backend. Override in subclasses."""
        raise NotImplementedError("Subclasses must define charm_name")

    @property
    def charm_channel(self) -> str:
        """Charm channel for this backend. Override in subclasses."""
        return "stable"

    @property
    def charm_revision(self) -> Optional[int]:
        """Charm revision for this backend. Override in subclasses."""
        return None

    @property
    def charm_base(self) -> str:
        """Charm base for this backend. Override in subclasses."""
        return "ubuntu@22.04"

    @property
    def backend_endpoint(self) -> str:
        """Backend endpoint name for integration. Override in subclasses."""
        return "cinder-volume"

    @property
    def units(self) -> int:
        """Number of units to deploy. Override in subclasses."""
        return 1

    @property
    def additional_integrations(self) -> List[str]:
        """Additional integrations for this backend. Override in subclasses."""
        return []

    def _get_backend_config(self, config: StorageBackendConfig) -> Dict[str, Any]:
        """Convert user config to charm-specific config. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement _get_backend_config")

    @abstractmethod
    def get_terraform_variables(
        self, backend_name: str, config: StorageBackendConfig, model: str
    ) -> Dict[str, Any]:
        """Generate Terraform variables for this backend. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement get_terraform_variables")

    def get_field_mapping(self) -> Dict[str, str]:
        """Get mapping from config fields to charm config options.

        Maps Pydantic field names (with underscores) to charm config option
        names (with hyphens). Uses the config_class to automatically generate
        the mapping from Pydantic model fields.
        """
        config_class = self.config_class
        # Use model_fields for Pydantic v2
        model_fields = getattr(config_class, "model_fields", {})
        field_names = model_fields.keys() if model_fields else []

        return {key: key.replace("_", "-") for key in field_names}

    @abstractmethod
    def prompt_for_config(self, backend_name: str) -> StorageBackendConfig:
        """Prompt user for backend-specific configuration. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement prompt_for_config")
