# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Base step classes for storage backend implementations.

This module provides base step classes that facilitate the implementation
of storage backend steps. Backends can inherit from these base classes
to get common functionality while customizing specific behavior.
"""

import logging
from typing import TYPE_CHECKING, Any, Callable

import pydantic
import tenacity
from rich.console import Console
from rich.status import Status

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    StorageBackendNotFoundException,
)
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
from sunbeam.core.questions import (
    ConfirmQuestion,
    PasswordPromptQuestion,
    PromptQuestion,
    Question,
    QuestionBank,
    load_answers,
    write_answers,
)
from sunbeam.core.terraform import (
    TerraformException,
    TerraformHelper,
    TerraformStateLockedException,
)
from sunbeam.storage.models import SecretDictField

if TYPE_CHECKING:
    from sunbeam.storage.base import StorageBackendBase

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

            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.error(f"Failed to validate storage prerequisites: {e}")
            return Result(ResultType.FAILED, str(e))


def basemodel_validator(
    model: type[pydantic.BaseModel],
) -> Callable[[str], Callable[[Any], None]]:
    """Return a factory producing value validators for Pydantic model fields."""
    validator = model.__pydantic_validator__
    fields = dict(model.model_fields.items())
    constructed = model.model_construct()

    def field_validator(field: str) -> Callable[[Any], None]:
        if field not in fields:
            raise ValueError(f"{model.__name__} has no field named {field!r}")

        def value_validator(value: Any) -> None:
            try:
                validator.validate_assignment(constructed, field, value)
            except pydantic.ValidationError as exc:
                messages: list[str] = []
                for error in exc.errors():
                    location = ".".join(str(part) for part in error.get("loc", ()))
                    message = error.get("msg", str(error))
                    if location:
                        messages.append(f"{location}: {message}")
                    else:
                        messages.append(message)
                raise ValueError("; ".join(messages))

        return value_validator

    return field_validator


def generate_questions_from_config(
    config_type: type[pydantic.BaseModel], *, optional: bool = False
) -> dict[str, Question]:
    questions = {}  # type: ignore
    field_validator = basemodel_validator(config_type)
    for field, finfo in config_type.model_fields.items():
        if optional and finfo.is_required():
            continue
        if not optional and not finfo.is_required():
            continue
        question_type: type[Question] = PromptQuestion
        for constraint in finfo.metadata:
            if isinstance(constraint, SecretDictField):
                question_type = PasswordPromptQuestion
        prompt_suffix = " (optional)" if optional else ""
        questions[field] = question_type(
            f"Enter value for {field!r}{prompt_suffix}",
            description=finfo.description,
            validation_function=field_validator(field),
        )
    return questions


class BaseStorageBackendDeployStep(BaseStep):
    """Base class for storage backend deployment steps.

    Provides common deployment functionality that backends can inherit from
    and customize as needed.
    """

    def __init__(
        self,
        deployment: Deployment,
        client: Client,
        tfhelper: TerraformHelper,
        jhelper: JujuHelper,
        manifest: Manifest,
        preseed: dict,
        backend_name: str,
        backend_instance: "StorageBackendBase",
        model: str,
        accept_defaults: bool = False,
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
        self.backend_instance = backend_instance
        self.model = model
        self.preseed = preseed
        self.accept_defaults = accept_defaults
        self.variables: dict = {}
        self.config_key = self.backend_instance.config_key(self.backend_name)

    def prompt(
        self,
        console: Console | None = None,
        show_hint: bool = False,
    ) -> None:
        """Determines if the step can take input from the user.

        Prompts are used by Steps to gather the necessary input prior to
        running the step. Steps should not expect that the prompt will be
        available and should provide a reasonable default where possible.
        """
        self.variables = load_answers(self.client, self.config_key)

        preseed = {}
        if self.manifest and self.manifest.storage:
            if backends := self.manifest.storage.root.get(
                self.backend_instance.backend_type
            ):
                if crt := backends.root.get(self.backend_name):
                    # Since question generation depends on field name,
                    # do not dump by alias
                    preseed = crt.model_dump(by_alias=False)["config"]

        # Preseed from user is higher priority than manifest
        preseed.update(self.preseed)

        manifest_configured = False

        if preseed:
            manifest_configured = True

        required_questions_bank = QuestionBank(
            questions=generate_questions_from_config(
                self.backend_instance.config_type()
            ),
            console=console,
            preseed=preseed,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )
        for name, question in required_questions_bank.questions.items():
            answer = question.ask()
            while not answer:
                answer = question.ask()
            self.variables[name] = answer

        res = ConfirmQuestion(
            "Set optional configurations?",
            accept_defaults=self.accept_defaults,
            default_value=manifest_configured,
        ).ask()

        if not res:
            write_answers(self.client, self.config_key, self.variables)
            return

        optional_questions_bank = QuestionBank(
            questions=generate_questions_from_config(
                self.backend_instance.config_type(), optional=True
            ),
            console=console,
            preseed=preseed,
            previous_answers=self.variables,
            accept_defaults=self.accept_defaults,
            show_hint=show_hint,
        )

        for name, question in optional_questions_bank.questions.items():
            if ConfirmQuestion(
                f"Configure option {name!r}?",
                accept_defaults=self.accept_defaults,
                default_value=name in preseed,
            ).ask():
                self.variables[name] = question.ask()
            else:
                # Remove variable if previously set for
                # subsequent runs
                self.variables.pop(name, None)

        try:
            # Validate configuration
            self.backend_instance.config_type().model_validate(
                self.variables, by_name=True
            )
        except pydantic.ValidationError as e:
            LOG.error(f"Invalid configuration: {e}")
            raise e

        write_answers(self.client, self.config_key, self.variables)

    def has_prompts(self) -> bool:
        """Returns true if the step has prompts that it can ask the user.

        :return: True if the step can ask the user for prompts,
                 False otherwise
        """
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
        """Deploy the storage backend using Terraform."""
        # Ensure fresh Juju credentials and Terraform env before applying
        try:
            self.deployment.reload_tfhelpers()
        except Exception as cred_err:
            LOG.debug(f"Failed to reload credentials/env: {cred_err}")

        # Merge with existing backends so we don't overwrite them
        backend_key = self.backend_name
        try:
            tfvars = read_config(self.client, self.backend_instance.tfvar_config_key)
        except Exception:
            tfvars = {}

        model = self.jhelper.get_model(self.model)

        backends = tfvars.setdefault("backends", {})

        tfvars["model"] = model["model-uuid"]

        # Remove backend if in current config, to ensure we remove the keys
        # no longer used
        backends.pop(backend_key, None)
        validated_config = self.backend_instance.config_type().model_validate(
            self.variables, by_name=True
        )
        backends[backend_key] = self.backend_instance.build_terraform_vars(
            self.deployment,
            self.manifest,
            self.backend_name,
            validated_config,
        )
        try:
            # Update Terraform variables and apply with merged map
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tfvars,
            )
        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            raise e
        except Exception as e:
            LOG.error(
                f"Failed to deploy {self.backend_instance.display_name} "
                f"backend {self.backend_name}: {e}"
            )
            return Result(ResultType.FAILED, str(e))
        # Let's save backend if not present
        self.client.cluster.add_storage_backend(
            self.backend_name,
            self.backend_instance.backend_type,
            validated_config.model_dump(exclude_none=True, by_alias=True),
            self.backend_instance.principal_application,
            model["model-uuid"],
        )

        try:
            self.jhelper.wait_application_ready(
                self.backend_name,
                model["model-uuid"],
                accepted_status=self.get_accepted_application_status(),
                timeout=self.get_application_timeout(),
            )
        except TimeoutError as e:
            LOG.warning(str(e))
            return Result(ResultType.FAILED, str(e))

        self.backend_instance.enable_backend(self.client)

        console.print(
            f"Successfully deployed {self.backend_instance.display_name} "
            f"backend {self.backend_name!r}"
        )
        return Result(ResultType.COMPLETED)

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds. Override for custom timeout."""
        return 1200  # 20 minutes, same as cinder-volume

    def get_accepted_application_status(self) -> list[str]:
        """Return accepted application status."""
        return ["active"]


class BaseStorageBackendDestroyStep(BaseStep):
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
        # Ensure fresh Juju credentials and Terraform env before destroying/applying
        try:
            self.deployment.reload_tfhelpers()
        except Exception as cred_err:
            LOG.debug(f"Failed to reload credentials/env: {cred_err}")

        # First, read and validate the current configuration
        try:
            tfvars = read_config(self.client, self.backend_instance.tfvar_config_key)
        except ConfigItemNotFoundException:
            LOG.warning(f"No configuration found for backend {self.backend_name}")
            tfvars = {}

        backends = tfvars.get("backends", {})

        # Drop backend from current configuration
        backends.pop(self.backend_name, None)

        # For removal: update config and apply atomically
        LOG.info(f"Performing removal for backend {self.backend_name}")
        LOG.info(f"Remaining backends after removal: {list(tfvars['backends'].keys())}")

        # First update the configuration
        update_config(
            self.client,
            self.backend_instance.tfvar_config_key,
            tfvars,
        )
        LOG.info("Configuration updated, now running terraform apply...")

        try:
            LOG.info(
                f"Writing Terraform variables with backends: "
                f"{list(tfvars.get('backends', {}).keys())}"
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tfvars,
            )
        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            LOG.debug("Error: Terraform state locked")
            raise e
        except TerraformException:
            # Restore the backend configuration if apply fails
            LOG.debug("Terraform apply failed", exc_info=True)
            return Result(
                ResultType.FAILED,
                f"Failed to destroy backend {self.backend_name!r}",
            )

        try:
            self.client.cluster.delete_storage_backend(self.backend_name)
        except StorageBackendNotFoundException:
            LOG.debug(f"Backend {self.backend_name} not found in clusterd")

        try:
            # Wipe previously saved answers
            self.client.cluster.delete_config(
                self.backend_instance.config_key(self.backend_name)
            )
        except ConfigItemNotFoundException:
            LOG.debug(
                f"Configuration for backend {self.backend_name} not found in clusterd"
            )

        return Result(ResultType.COMPLETED)

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return 1200  # 20 minutes, same as cinder-volume
