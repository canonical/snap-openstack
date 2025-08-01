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

import tenacity
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ConfigItemNotFoundException
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    friendly_terraform_lock_retry_callback,
    read_config,
    update_config,
)
from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import (
    ControllerNotFoundException,
    ControllerNotReachableException,
    JujuException,
    JujuHelper,
)
from sunbeam.core.manifest import Manifest
from sunbeam.core.terraform import TerraformHelper, TerraformStateLockedException

from .models import BackendNotFoundException, StorageBackendConfig

if TYPE_CHECKING:
    from .base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()


class ValidateStoragePrerequisitesStep(BaseStep):
    """Validate that Sunbeam is bootstrapped and storage role is deployed."""

    def __init__(self, deployment: Deployment, client: Client, jhelper: JujuHelper):
        super().__init__(
            "Validate storage prerequisites",
            "Checking Sunbeam bootstrap and storage role deployment",
        )
        self.deployment = deployment
        self.client = client
        self.jhelper = jhelper
        self.OPENSTACK_MACHINE_MODEL = self.deployment.openstack_machines_model

    def _check_juju_authentication(self) -> Result:
        """Check if the current user is authenticated with Juju."""
        try:
            # Use the existing JujuHelper to check authentication
            # If we can list models, we're authenticated
            models = self.jhelper.models()
            LOG.debug(
                f"Juju authentication check successful, found {len(models)} models"
            )
            return Result(ResultType.COMPLETED)

        except ControllerNotFoundException:
            return Result(
                ResultType.FAILED,
                "Juju controller not found. Please ensure Sunbeam is bootstrapped:\n"
                "'sunbeam cluster bootstrap'",
            )
        except ControllerNotReachableException:
            return Result(
                ResultType.FAILED,
                "Juju controller not reachable. Please check network connectivity\n"
                "or re-authenticate with 'sunbeam utils juju-login'",
            )
        except JujuException as e:
            # Check if it's an authentication-related error
            error_msg = str(e).lower()
            if any(
                keyword in error_msg
                for keyword in [
                    "not logged in",
                    "authentication",
                    "unauthorized",
                    "permission denied",
                    "please enter password",
                ]
            ):
                return Result(
                    ResultType.FAILED,
                    "Not authenticated with Juju controller. Please run:\n"
                    "'sunbeam utils juju-login'\n"
                    "or authenticate manually with 'juju login'",
                )
            else:
                return Result(ResultType.FAILED, f"Juju operation failed: {e}")
        except Exception as e:
            return Result(
                ResultType.FAILED, f"Failed to check Juju authentication: {e}"
            )

    def run(self, status: Status | None = None) -> Result:
        """Validate storage backend prerequisites."""
        try:
            # 0. Check Juju authentication first
            auth_result = self._check_juju_authentication()
            if auth_result.result_type != ResultType.COMPLETED:
                return auth_result

            # 1. Check if Sunbeam is bootstrapped
            is_bootstrapped = self.client.cluster.check_sunbeam_bootstrapped()
            if not is_bootstrapped:
                return Result(
                    ResultType.FAILED,
                    "Deployment not bootstrapped. Please run\n"
                    "'sunbeam cluster bootstrap' first.",
                )

            # 2. Check if OpenStack model exists
            if not self.jhelper.model_exists(self.OPENSTACK_MACHINE_MODEL):
                return Result(
                    ResultType.FAILED,
                    f"OpenStack model '{self.OPENSTACK_MACHINE_MODEL}' not found. "
                    "Please deploy OpenStack first with\n"
                    "'sunbeam configure --openstack'.",
                )

            # 3. Check if storage role is deployed (at least one storage node)
            storage_nodes = self.client.cluster.list_nodes_by_role("storage")
            if not storage_nodes:
                return Result(
                    ResultType.FAILED,
                    "No storage role found. Please add storage nodes to the cluster "
                    "before deploying storage backends.",
                )

            # 4. Check if cinder-volume application exists in OpenStack model
            try:
                cinder_volume_app = self.jhelper.get_application(
                    "cinder-volume", self.OPENSTACK_MACHINE_MODEL
                )
                if not cinder_volume_app:
                    return Result(
                        ResultType.FAILED,
                        "cinder-volume application not found in OpenStack model. "
                        "Please deploy OpenStack storage services first.",
                    )
            except Exception as e:
                LOG.debug(f"Failed to check cinder-volume application: {e}")
                return Result(
                    ResultType.FAILED,
                    "Unable to verify cinder-volume application. "
                    "Please ensure OpenStack storage services are deployed.",
                )

            console.print("✓ All storage prerequisites validated successfully")
            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.error(f"Failed to validate storage prerequisites: {e}")
            return Result(ResultType.FAILED, str(e))


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

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=friendly_terraform_lock_retry_callback,
        before_sleep=lambda retry_state: console.print(
            f"Terraform state locked, retrying in 60 seconds... "
            f"(attempt {retry_state.attempt_number}/5)"
        ),
    )
    def run(self, status: Status | None = None) -> Result:
        """Deploy the storage backend using Terraform."""
        try:
            # Ensure fresh Juju credentials and Terraform env before applying
            try:
                self.deployment.reload_tfhelpers()
            except Exception as cred_err:
                LOG.debug(f"Failed to reload credentials/env: {cred_err}")

            # Get Terraform variables for this backend (contains a single backend entry)
            tf_vars = self.get_terraform_variables()

            # Merge with existing backends so we don't overwrite them
            backend_key = f"{self.backend_instance.name}_backends"
            try:
                current_tfvars = read_config(
                    self.client, self.backend_instance.tfvar_config_key
                )
                current_backends = (
                    current_tfvars.get(backend_key, {}) if current_tfvars else {}
                )
            except Exception:
                current_backends = {}

            # The new backend map is at tf_vars[backend_key]
            new_backends = tf_vars.get(backend_key, {})
            merged_backends = {**current_backends, **new_backends}
            tf_vars[backend_key] = merged_backends

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

        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            raise e
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

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=friendly_terraform_lock_retry_callback,
        before_sleep=lambda retry_state: console.print(
            f"Terraform state locked, retrying in 60 seconds... "
            f"(attempt {retry_state.attempt_number}/5)"
        ),
    )
    def run(self, status: Status | None = None) -> Result:
        """Run the destroy step atomically.

        This step removes the backend from the Terraform configuration
        and applies the changes to destroy the associated resources.
        The operation is atomic: either it succeeds completely or fails
        without modifying the configuration.
        """
        try:
            # Ensure fresh Juju credentials and Terraform env before destroying/applying
            try:
                self.deployment.reload_tfhelpers()
            except Exception as cred_err:
                LOG.debug(f"Failed to reload credentials/env: {cred_err}")

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
                            f"{list(tf_vars.get(backend_key, {}).keys())}"
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

        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            raise e
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

    @tenacity.retry(
        wait=tenacity.wait_fixed(60),
        stop=tenacity.stop_after_delay(300),
        retry=tenacity.retry_if_exception_type(TerraformStateLockedException),
        retry_error_callback=friendly_terraform_lock_retry_callback,
        before_sleep=lambda retry_state: console.print(
            f"Terraform state locked, retrying in 60 seconds... "
            f"(attempt {retry_state.attempt_number}/5)"
        ),
    )
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

            # Handle update operation
            current_config = self.handle_update_operation(current_config)
            operation_type = "update"

            # Save updated configuration and apply with updated tfvars
            update_config(
                self.client, self.backend_instance.tfvar_config_key, current_config
            )

            # Ensure fresh Juju credentials and Terraform env before applying
            try:
                self.deployment.reload_tfhelpers()
            except Exception as cred_err:
                LOG.debug(f"Failed to reload credentials/env: {cred_err}")

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

        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            raise e
        except Exception as e:
            LOG.error(
                f"Failed to update {self.backend_instance.display_name} "
                f"backend {self.backend_name} configuration: {e}"
            )
            return Result(ResultType.FAILED, str(e))
