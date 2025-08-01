# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Base step classes for storage backend implementations.

This module provides base step classes that facilitate the implementation
of storage backend steps. Backends can inherit from these base classes
to get common functionality while customizing specific behavior.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict

from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import BaseStep, Result, ResultType, read_config, update_config
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import JujuHelper
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper

from .models import BackendNotFoundException, StorageBackendConfig

if TYPE_CHECKING:
    from .base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class BaseStorageBackendDeployStep(BaseStep, ABC):
    """Base class for storage backend deployment steps.

    Provides common deployment functionality that backends can inherit from
    and customize as needed. Backends should override get_terraform_variables()
    and can override other methods for custom behavior.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        backend_config: StorageBackendConfig,
        backend_instance: "StorageBackendBase",
        model: str,
    ):
        super().__init__(
            f"Deploy {backend_instance.display_name} backend {backend_name}",
            f"Deploying {backend_instance.display_name} storage backend {backend_name}",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.backend_name = backend_name
        self.backend_config = backend_config
        self.backend_instance = backend_instance
        self.model = model

    @abstractmethod
    def get_terraform_variables(self) -> Dict[str, Any]:
        """Get Terraform variables for this backend deployment.

        Backends must implement this method to provide their specific
        Terraform variables for deployment.
        """
        pass

    def run(self, status: Status | None = None) -> Result:
        """Deploy the storage backend using Terraform."""
        try:
            # Get Terraform variables for this backend (contains a single backend entry)
            tf_vars = self.get_terraform_variables()

            # Merge with existing backends so we don't overwrite them
            try:
                current_tfvars = read_config(
                    self.client, self.backend_instance.tfvar_config_key
                )
                current_backends = (
                    current_tfvars.get("hitachi_backends", {}) if current_tfvars else {}
                )
            except Exception:
                current_backends = {}

            # The new backend map is at tf_vars["hitachi_backends"]
            new_backends = tf_vars.get("hitachi_backends", {})
            merged_backends = {**current_backends, **new_backends}
            tf_vars["hitachi_backends"] = merged_backends

            # Update Terraform variables and apply with merged map
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tf_vars,
            )

            console.print(
                f"Successfully deployed {self.backend_instance.display_name} "
                f"backend '{self.backend_name}'"
            )
            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.error(
                f"Failed to deploy {self.backend_instance.display_name} "
                f"backend {self.backend_name}: {e}"
            )
            return Result(ResultType.FAILED, str(e))

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds. Override for custom timeout."""
        return 1200  # 20 minutes, same as cinder-volume

    def get_accepted_application_status(self) -> list[str]:
        """Return accepted application status."""
        return ["active", "waiting"]


class BaseStorageBackendDestroyStep(BaseStep, ABC):
    """Base class for storage backend destruction steps.

    Provides common destruction functionality that backends can inherit from
    and customize as needed. Handles Terraform state cleanup and configuration
    removal from clusterd.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        backend_name: str,
        backend_instance: "StorageBackendBase",
        model: str,
    ):
        super().__init__(
            f"Destroy {backend_instance.display_name} backend {backend_name}",
            f"Destroying {backend_instance.display_name} storage "
            f"backend {backend_name}",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.backend_name = backend_name
        self.backend_instance = backend_instance
        self.model = model

    def should_destroy_all_resources(self) -> bool:
        """Check if all resources should be destroyed (no backends left).

        Override this method if backend has custom logic for determining
        when to destroy all resources vs just removing configuration.
        """
        try:
            current_config = read_config(
                self.client, self.backend_instance.tfvar_config_key
            )

            backend_key = (
                f"{self.backend_instance.name}_backends"  # e.g., "hitachi_backends"
            )

            if backend_key in current_config:
                backends = current_config[backend_key]
            else:
                raise BackendNotFoundException(
                    f"Backend '{self.backend_name}' not found"
                )

            # Remove this backend from the count
            backends_without_current = {
                k: v for k, v in backends.items() if k != self.backend_name
            }
            return len(backends_without_current) == 0
        except ConfigItemNotFoundException:
            return True

    def run(self, status: Status | None = None) -> Result:
        """Run the destroy step atomically.

        This step removes the backend from the Terraform configuration
        and applies the changes to destroy the associated resources.
        The operation is atomic: either it succeeds completely or fails
        without modifying the configuration.
        """
        try:
            # First, read and validate the current configuration
            try:
                current_config = read_config(
                    self.client, self.backend_instance.tfvar_config_key
                )
            except ConfigItemNotFoundException:
                LOG.warning(f"No configuration found for backend {self.backend_name}")
                raise BackendNotFoundException(
                    f"No Terraform configuration found for backend "
                    f"'{self.backend_name}'"
                )

            # Check if backend exists in configuration
            backend_key = (
                f"{self.backend_instance.name}_backends"  # e.g., "hitachi_backends"
            )

            if (
                backend_key not in current_config
                or self.backend_name not in current_config[backend_key]
            ):
                LOG.warning(f"Backend {self.backend_name} not found in configuration")
                raise BackendNotFoundException(
                    f"Backend '{self.backend_name}' not found in Terraform"
                    f"configuration. This may indicate a state inconsistency."
                )

            # Create a backup of the backend configuration before removal
            backend_backup = current_config[backend_key][self.backend_name].copy()

            # Remove backend from configuration (in memory only)
            del current_config[backend_key][self.backend_name]

            # Determine if we need to destroy all resources or just apply changes
            destroy_all = self.should_destroy_all_resources()

            try:
                if destroy_all:
                    # For complete destruction: first update config, then destroy
                    # If destroy fails, we can restore the config
                    update_config(
                        self.client,
                        self.backend_instance.tfvar_config_key,
                        current_config,
                    )

                    try:
                        self.tfhelper.destroy()
                    except Exception as destroy_error:
                        # Restore the backend configuration if destroy fails
                        LOG.error(
                            f"""Terraform destroy failed,
                            restoring configuration: {destroy_error}"""
                        )
                        current_config[backend_key][self.backend_name] = backend_backup
                        update_config(
                            self.client,
                            self.backend_instance.tfvar_config_key,
                            current_config,
                        )
                        raise destroy_error
                else:
                    # For partial removal: update config and apply atomically
                    LOG.info(
                        f"Performing partial removal for backend {self.backend_name}"
                    )
                    LOG.info(
                        f"Remaining backends after removal: "
                        f"{list(current_config[backend_key].keys())}"
                    )

                    # First update the configuration
                    update_config(
                        self.client,
                        self.backend_instance.tfvar_config_key,
                        current_config,
                    )
                    LOG.info("Configuration updated, now running terraform apply...")

                    try:
                        LOG.info("Starting terraform apply for partial removal")
                        # CRITICAL: Write the updated Terraform variables
                        # before applying
                        # This was missing and causing partial removal to fail!
                        LOG.info("Writing updated Terraform variables...")

                        # Get the updated Terraform variables from the current config
                        tf_vars = current_config.copy()
                        LOG.info(
                            f"Writing Terraform variables with backends: "
                            f"{list(tf_vars.get('hitachi_backends', {}).keys())}"
                        )

                        self.tfhelper.write_tfvars(tf_vars)
                        LOG.info("Terraform variables written, now applying...")
                        self.tfhelper.apply()
                        LOG.info(
                            "Terraform apply completed successfully for partial removal"
                        )
                    except Exception as apply_error:
                        # Restore the backend configuration if apply fails
                        LOG.error(
                            f"Terraform apply failed, restoring configuration: "
                            f"{apply_error}"
                        )
                        current_config[backend_key][self.backend_name] = backend_backup
                        update_config(
                            self.client,
                            self.backend_instance.tfvar_config_key,
                            current_config,
                        )
                        raise apply_error

            except Exception as tf_error:
                # Any Terraform operation failure should be propagated
                raise tf_error

            console.print(
                f"Successfully removed {self.backend_instance.display_name} "
                f"backend '{self.backend_name}'"
            )
            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.error(
                f"Failed to destroy {self.backend_instance.display_name} "
                f"backend {self.backend_name}: {e}"
            )
            return Result(ResultType.FAILED, str(e))

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return 1200  # 20 minutes, same as cinder-volume


class BaseStorageBackendConfigUpdateStep(BaseStep, ABC):
    """Base class for storage backend configuration update steps.

    Provides common configuration update functionality that backends can inherit from
    and customize as needed. Handles configuration updates and reset operations.
    """

    def __init__(
        self,
        deployment: Deployment,
        backend_instance: "StorageBackendBase",
        backend_name: str,
        config_updates: Dict[str, Any],
    ):
        super().__init__(
            f"Update {backend_instance.display_name} backend config {backend_name}",
            f"Updating {backend_instance.display_name} storage backend "
            f"configuration for {backend_name}",
        )
        self.deployment = deployment
        self.backend_instance = backend_instance
        self.backend_name = backend_name
        self.config_updates = config_updates
        self.client = deployment.get_client()
        self.tfhelper = deployment.get_tfhelper(backend_instance.tfplan)

    def is_reset_operation(self) -> bool:
        """Check if this is a reset operation."""
        return "_reset_keys" in self.config_updates

    def get_reset_keys(self) -> list[str]:
        """Get the keys to reset. Only valid if is_reset_operation() returns True."""
        return self.config_updates.get("_reset_keys", [])

    def handle_reset_operation(self, current_config: Dict[str, Any]) -> Dict[str, Any]:
        """Handle reset operation. Override for custom reset logic.

        Args:
            current_config: Current backend configuration

        Returns:
            Updated configuration with reset keys set to their default values
        """
        reset_keys = self.get_reset_keys()

        # Check new format (backend-specific keys only)
        backend_key = (
            f"{self.backend_instance.name}_backends"  # e.g., "hitachi_backends"
        )

        if (
            backend_key in current_config
            and self.backend_name in current_config[backend_key]
        ):
            backend_config = current_config[backend_key][self.backend_name]
        else:
            return current_config

        if "charm_config" in backend_config:
            # Get default values from the backend's config class
            config_class = self.backend_instance.config_class

            # Create a minimal instance with defaults to get default values
            # We need to provide required fields to create the instance
            try:
                # Try to create instance with minimal required fields
                # Use only the base StorageBackendConfig fields
                default_instance = config_class(name="dummy")
            except Exception:
                # If that fails, try to get defaults from field definitions
                default_instance = None

            for key in reset_keys:
                if default_instance and hasattr(default_instance, key):
                    # Set to default value from pydantic model instance
                    default_value = getattr(default_instance, key)
                    backend_config["charm_config"][key] = default_value
                else:
                    # Try to get default from field definition
                    model_fields = getattr(config_class, "model_fields", {})
                    field_info = model_fields.get(key)
                    if (
                        field_info
                        and hasattr(field_info, "default")
                        and field_info.default is not None
                    ):
                        backend_config["charm_config"][key] = field_info.default
                    else:
                        # If no default available, remove the key
                        backend_config["charm_config"].pop(key, None)

        return current_config

    def handle_update_operation(self, current_config: Dict[str, Any]) -> Dict[str, Any]:
        """Handle configuration update operation. Override for custom update logic.

        Args:
            current_config: Current backend configuration

        Returns:
            Updated configuration with new values applied
        """
        # Get backend config from new format only
        backend_key = (
            f"{self.backend_instance.name}_backends"  # e.g., "hitachi_backends"
        )
        backend_config = current_config[backend_key][self.backend_name]
        if "charm_config" not in backend_config:
            backend_config["charm_config"] = {}

        # Apply configuration updates (excluding reset keys) with field mapping
        updates = {k: v for k, v in self.config_updates.items() if k != "_reset_keys"}

        # Apply field mapping to convert internal field names to charm field names
        field_mapping = self.backend_instance.get_field_mapping()
        mapped_updates = {}
        for key, value in updates.items():
            # Use field mapping if available, otherwise use the key as-is
            charm_key = field_mapping.get(key, key)
            mapped_updates[charm_key] = value

        backend_config["charm_config"].update(mapped_updates)

        return current_config

    def run(self, status: Status | None = None) -> Result:
        """Update the storage backend configuration using Terraform."""
        # Read current configuration
        try:
            current_config = read_config(
                self.client, self.backend_instance.tfvar_config_key
            )

            # Check new format (backend-specific keys only)
            backend_key = (
                f"{self.backend_instance.name}_backends"  # e.g., "hitachi_backends"
            )

            if (
                backend_key not in current_config
                or self.backend_name not in current_config[backend_key]
            ):
                return Result(
                    ResultType.FAILED, f"Backend {self.backend_name} not found"
                )

            # Handle reset or update operation
            if self.is_reset_operation():
                current_config = self.handle_reset_operation(current_config)
                operation_type = "reset"
            else:
                current_config = self.handle_update_operation(current_config)
                operation_type = "update"

            # Save updated configuration and apply with updated tfvars
            update_config(
                self.client, self.backend_instance.tfvar_config_key, current_config
            )

            # Write the updated tfvars and apply
            self.tfhelper.write_tfvars(current_config)
            self.tfhelper.apply()

            console.print(
                f"Successfully {operation_type}d "
                f"{self.backend_instance.display_name} backend "
                f"'{self.backend_name}' configuration"
            )
            return Result(ResultType.COMPLETED)

        except ConfigItemNotFoundException:
            return Result(
                ResultType.FAILED,
                f"Configuration not found for backend {self.backend_name}",
            )

        except Exception as e:
            LOG.error(
                f"Failed to update {self.backend_instance.display_name} "
                f"backend {self.backend_name} configuration: {e}"
            )
            return Result(ResultType.FAILED, str(e))
