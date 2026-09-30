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

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    StorageBackendNotFoundException,
)
from sunbeam.core.common import (
    BaseStep,
    Result,
    ResultType,
    Role,
    StepContext,
    friendly_terraform_lock_retry_callback,
    read_config,
    update_config,
)
from sunbeam.core.deployment import Deployment, Networks
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
from sunbeam.steps.cinder_volume import (
    APPLICATION,
    CINDER_VOLUME_APP_TIMEOUT,
    get_mandatory_control_plane_offers,
    get_optional_control_plane_offers,
)
from sunbeam.storage.models import SecretDictField
from sunbeam.versions import CINDER_VOLUME_CHARM

if TYPE_CHECKING:
    from sunbeam.storage.base import StorageBackendBase

LOG = logging.getLogger(__name__)
console = Console()

PRINCIPAL_HA_APPLICATION = "cinder-volume"
PRINCIPAL_NON_HA_APPLICATION = "cinder-volume-noha"
SNAP_NAME_HA = "cinder-volume"
SNAP_NAME_NON_HA = "cinder-volume_noha"


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
                "Juju authentication check successful, found %d models", len(models)
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

    def run(self, context: StepContext) -> Result:
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
                LOG.debug("Failed to check cinder-volume application: %r", e)
                return Result(
                    ResultType.FAILED,
                    "Unable to verify cinder-volume application. "
                    "Please ensure OpenStack storage services are deployed.",
                )

            return Result(ResultType.COMPLETED)

        except Exception as e:
            LOG.error("Failed to validate storage prerequisites: %r", e)
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


def prompt_backend_config(
    client: Client,
    backend_instance: "StorageBackendBase",
    manifest: Manifest,
    backend_name: str,
    preseed: dict,
    console: Console | None = None,
    accept_defaults: bool = False,
    show_hint: bool = True,
) -> dict:
    """Prompt for backend configuration, validating and persisting answers.

    Merges saved answers, manifest preseed and user preseed (CLI values),
    prompting only for configuration that is still missing. Called before
    the deployment plan is built so decisions that depend on the
    configuration (e.g. HA support) can be made up front.

    Returns the validated configuration values keyed by field name.
    """
    config_key = backend_instance.config_key(backend_name)
    variables = load_answers(client, config_key)

    merged_preseed = {}
    if manifest and manifest.storage:
        if backends := manifest.storage.root.get(backend_instance.backend_type):
            if crt := backends.root.get(backend_name):
                # Since question generation depends on field name,
                # do not dump by alias
                if crt.config is not None:
                    merged_preseed = crt.config.model_dump(by_alias=False)

    # Preseed from user is higher priority than manifest
    merged_preseed.update(preseed)

    required_questions_bank = QuestionBank(
        questions=generate_questions_from_config(backend_instance.config_type()),
        console=console,
        preseed=merged_preseed,
        previous_answers=variables,
        accept_defaults=accept_defaults,
        show_hint=show_hint,
    )
    for name, question in required_questions_bank.questions.items():
        answer = question.ask()
        while not answer:
            answer = question.ask()
        variables[name] = answer

    res = ConfirmQuestion(
        "Set optional configurations?",
        accept_defaults=accept_defaults,
        default_value=bool(merged_preseed),
    ).ask()

    if res:
        optional_questions_bank = QuestionBank(
            questions=generate_questions_from_config(
                backend_instance.config_type(), optional=True
            ),
            console=console,
            preseed=merged_preseed,
            previous_answers=variables,
            accept_defaults=accept_defaults,
            show_hint=show_hint,
        )

        for name, question in optional_questions_bank.questions.items():
            if ConfirmQuestion(
                f"Configure option {name!r}?",
                accept_defaults=accept_defaults,
                default_value=name in merged_preseed,
            ).ask():
                variables[name] = question.ask()
            else:
                # Remove variable if previously set for
                # subsequent runs
                variables.pop(name, None)

    try:
        # Validate configuration
        backend_instance.config_type().model_validate(variables, by_name=True)
    except pydantic.ValidationError as e:
        LOG.error("Invalid configuration: %r", e)
        raise e

    write_answers(client, config_key, variables)
    return variables


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
        # Configuration is collected and validated before the plan is built
        # (prompt_backend_config), so the preseed carries the full config
        self.variables = dict(preseed)
        self.config_key = self.backend_instance.config_key(self.backend_name)

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
    def run(self, context: StepContext) -> Result:
        """Deploy the storage backend using Terraform."""
        # Ensure fresh Juju credentials and Terraform env before applying
        try:
            self.deployment.reload_tfhelpers()
        except Exception as e:
            LOG.debug("Failed to reload credentials/env: %r", e)

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
                reporter=context.reporter,
            )
        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            raise e
        except Exception as e:
            LOG.error(
                "Failed to deploy %s backend %s: %r",
                self.backend_instance.display_name,
                self.backend_name,
                e,
            )
            return Result(ResultType.FAILED, str(e))
        # Let's save backend if not present
        data = {
            "name": self.backend_name,
            "backend_type": self.backend_instance.backend_type,
            "config": validated_config.model_dump(exclude_none=True, by_alias=True),
            "principal": (
                PRINCIPAL_HA_APPLICATION
                if self.backend_instance.supports_ha_for(validated_config)
                else PRINCIPAL_NON_HA_APPLICATION
            ),
            "model_uuid": model["model-uuid"],
        }
        try:
            self.client.cluster.get_storage_backend(self.backend_name)
            self.client.cluster.update_storage_backend(**data)
        except StorageBackendNotFoundException:
            self.client.cluster.add_storage_backend(**data)

        try:
            self.jhelper.wait_application_ready(
                self.backend_name,
                model["model-uuid"],
                accepted_status=self.get_accepted_application_status(),
                timeout=self.get_application_timeout(),
            )
        except TimeoutError as e:
            LOG.warning(
                "Timed out deploying %s backend %s: %r",
                self.backend_instance.display_name,
                self.backend_name,
                e,
            )
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
    def run(self, context: StepContext) -> Result:
        """Run the destroy step atomically.

        This step removes the backend from the Terraform configuration
        and applies the changes to destroy the associated resources.
        The operation is atomic: either it succeeds completely or fails
        without modifying the configuration.
        """
        # Ensure fresh Juju credentials and Terraform env before destroying/applying
        try:
            self.deployment.reload_tfhelpers()
        except Exception as e:
            LOG.debug("Failed to reload credentials/env: %r", e)

        # First, read and validate the current configuration
        try:
            tfvars = read_config(self.client, self.backend_instance.tfvar_config_key)
        except ConfigItemNotFoundException:
            LOG.warning("No configuration found for backend %s", self.backend_name)
            tfvars = {}

        backends = tfvars.get("backends", {})

        # Drop backend from current configuration
        backends.pop(self.backend_name, None)

        # For removal: update config and apply atomically
        LOG.info("Performing removal for backend %s", self.backend_name)
        LOG.info(
            "Remaining backends after removal: %s", list(tfvars["backends"].keys())
        )

        # First update the configuration
        update_config(
            self.client,
            self.backend_instance.tfvar_config_key,
            tfvars,
        )
        LOG.info("Configuration updated, now running Terraform apply")

        try:
            LOG.info(
                "Writing Terraform variables with backends: %s",
                list(tfvars.get("backends", {}).keys()),
            )
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tfvars,
                reporter=context.reporter,
            )
        except TerraformStateLockedException as e:
            # Bubble up to trigger retry
            LOG.debug("Terraform state locked")
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
            LOG.debug("Backend %s is not found in clusterd", self.backend_name)

        try:
            # Wipe previously saved answers
            self.client.cluster.delete_config(
                self.backend_instance.config_key(self.backend_name)
            )
        except ConfigItemNotFoundException:
            LOG.debug(
                "Configuration for backend %s is not found in clusterd",
                self.backend_name,
            )

        return Result(ResultType.COMPLETED)

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return 1200  # 20 minutes, same as cinder-volume


class DeploySpecificCinderVolumeStep(BaseStep):
    """Step to deploy the specific cinder-volume application.

    This step will deploy an instance of the cinder-volume charm
    in the given model.
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
        supports_ha: bool | None = None,
        extra_tfvars: dict | None = None,
    ):
        super().__init__(
            f"Deploy specific cinder-volume for backend {backend_name}",
            f"Deploying specific cinder-volume for backend {backend_name}",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.backend_name = backend_name
        self.backend_instance = backend_instance
        self.model = model
        # Falls back to the static property when the caller has no
        # configuration in hand (e.g. redeploys of recorded backends)
        self.supports_ha = (
            supports_ha if supports_ha is not None else backend_instance.supports_ha
        )
        self._offers: dict[str, str | None] | None = None
        self.extra_tfvars: dict = extra_tfvars or {}

    def is_skip(self, context: StepContext) -> Result:
        """Determine if the step should be skipped.

        Returns:
            Result indicating whether to skip the step.
        """
        nodes = self.client.cluster.list_nodes_by_role(Role.STORAGE.name.lower())
        if not nodes:
            return Result(ResultType.FAILED, "No storage nodes found in the cluster.")
        # For faster checks, skip if currently deployed backend
        # supports main cinder-volume
        if self.supports_ha:
            return Result(
                ResultType.SKIPPED,
                f"Backend {self.backend_name} supports main cinder-volume;"
                " skipping specific cinder-volume deployment.",
            )

        return Result(ResultType.COMPLETED)

    def _get_offers(self):
        if not self._offers:
            self._offers = get_mandatory_control_plane_offers(
                self.deployment.get_tfhelper("openstack-plan")
            )
        return self._offers

    def _get_telemetry_notifications_tfvar(self):
        feature_manager = self.deployment.get_feature_manager()
        return {
            "enable-telemetry-notifications": feature_manager.is_feature_enabled(
                self.deployment, "telemetry"
            )
        }

    def run(self, context: StepContext) -> Result:
        """Deploy the specific cinder-volume application."""
        try:
            tfvars = read_config(self.client, self.backend_instance.tfvar_config_key)
        except ConfigItemNotFoundException:
            tfvars = {}

        application_name = (
            PRINCIPAL_HA_APPLICATION
            if self.supports_ha
            else PRINCIPAL_NON_HA_APPLICATION
        )
        machine_ids = (
            tfvars.get("cinder-volumes", {})
            .get(application_name, {})
            .get("machine_ids")
        )
        if not machine_ids:
            nodes = self.client.cluster.list_nodes_by_role(Role.STORAGE.name.lower())
            machine_ids = sorted((node["machineid"] for node in nodes), key=int)
            if not self.supports_ha:
                machine_ids = machine_ids[:1]

        if not tfvars.get("model"):
            tfvars["model"] = self.jhelper.get_model(self.model)["model-uuid"]

        cinder_volume = self.manifest.core.software.charms[CINDER_VOLUME_CHARM]
        charm_config = dict(cinder_volume.config) if cinder_volume.config else {}
        if cinder_volume.model_extra:
            config_map = cinder_volume.model_extra.get("config-map", {})
            charm_config.update(config_map.get(application_name, {}))
        charm_config["snap-name"] = (
            SNAP_NAME_HA if self.supports_ha else SNAP_NAME_NON_HA
        )
        charm_revision = cinder_volume.revision
        charm_channel = cinder_volume.channel

        tfvars.setdefault("cinder-volumes", {})[application_name] = {
            "application_name": application_name,
            "charm_channel": charm_channel,
            "charm_revision": charm_revision,
            "charm_config": charm_config,
            "machine_ids": machine_ids,
            "endpoint_bindings": [
                {
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "amqp",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    "endpoint": "database",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    "endpoint": "cinder-volume",
                    "space": self.deployment.get_space(Networks.MANAGEMENT),
                },
                {
                    "endpoint": "identity-credentials",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    "endpoint": "receive-ca-cert",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
                {
                    # relation to cinder-api
                    "endpoint": "storage-backend",
                    "space": self.deployment.get_space(Networks.INTERNAL),
                },
            ],
        }
        tfvars["cinder-volumes"][application_name].update(self._get_offers())
        tfvars["cinder-volumes"][application_name].update(
            get_optional_control_plane_offers(
                self.deployment.get_tfhelper("openstack-plan")
            )
        )
        tfvars["cinder-volumes"][application_name].update(
            self._get_telemetry_notifications_tfvar()
        )
        # Any tfvars that needs override will take precedence from self.extra_tfvars
        # Example usage: When telemetry is enabled/disabled, telemetry feature can set
        # enable-telemetry-notifications using extra_tfvars
        tfvars["cinder-volumes"][application_name].update(self.extra_tfvars)

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tfvars,
                reporter=context.reporter,
            )
        except Exception as e:
            LOG.error(
                "Failed to deploy non-HA cinder-volume for backend %s: %r",
                self.backend_name,
                e,
            )
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_ready(
                application_name,
                tfvars["model"],
                accepted_status=self.get_accepted_application_status(),
                timeout=self.get_application_timeout(),
            )
        except TimeoutError as e:
            LOG.warning(
                "Timed out deploying non-HA cinder-volume for backend %s: %r",
                self.backend_name,
                e,
            )
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def get_accepted_application_status(self) -> list[str]:
        """Return accepted application status."""
        return ["active", "blocked"]

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return CINDER_VOLUME_APP_TIMEOUT  # 20 minutes, same as cinder-volume


class DestroySpecificCinderVolumeStep(BaseStep):
    """Step to destroy the specific cinder-volume application.

    This step will destroy an instance of the cinder-volume charm
    in the given model.
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
        principal_application: str | None = None,
    ):
        super().__init__(
            f"Destroy specific cinder-volume for backend {backend_name}",
            f"Destroying specific cinder-volume for backend {backend_name}",
        )
        self.deployment = deployment
        self.client = client
        self.tfhelper = tfhelper
        self.jhelper = jhelper
        self.manifest = manifest
        self.backend_name = backend_name
        self.backend_instance = backend_instance
        self.model = model
        # Prefer the principal recorded at deploy time; the static
        # property is only a fallback for never-deployed backends
        self.principal_application = (
            principal_application or backend_instance.principal_application
        )

    def is_skip(self, context: StepContext) -> Result:
        """Determine if the step should be skipped.

        Returns:
            Result indicating whether to skip the step.
        """
        if self.principal_application == APPLICATION:
            return Result(
                ResultType.SKIPPED,
                f"Backend {self.backend_name} does not use specific cinder-volume;"
                " skipping specific cinder-volume destruction.",
            )
        backends = self.client.cluster.get_storage_backends()
        for backend in backends.root:
            if self.principal_application == backend.principal:
                return Result(
                    ResultType.SKIPPED,
                    "Another backend is using the same cinder-volume instance;"
                    " skipping specific cinder-volume destruction.",
                )

        return Result(ResultType.COMPLETED)

    def run(self, context: StepContext) -> Result:
        """Destroy the specific cinder-volume application."""
        try:
            tfvars = read_config(self.client, self.backend_instance.tfvar_config_key)
        except ConfigItemNotFoundException:
            tfvars = {}

        tfvars.get("cinder-volumes", {}).pop(self.principal_application, None)

        try:
            self.tfhelper.update_tfvars_and_apply_tf(
                self.client,
                self.manifest,
                tfvar_config=self.backend_instance.tfvar_config_key,
                override_tfvars=tfvars,
                reporter=context.reporter,
            )
        except Exception as e:
            LOG.error(
                "Failed to destroy non-HA cinder-volume for backend %s: %r",
                self.backend_name,
                e,
            )
            return Result(ResultType.FAILED, str(e))

        try:
            self.jhelper.wait_application_gone(
                [self.principal_application],
                self.model,
                timeout=self.get_application_timeout(),
            )
        except TimeoutError as e:
            LOG.warning(
                "Timed out destroying non-HA cinder-volume for backend %s: %r",
                self.backend_name,
                e,
            )
            return Result(ResultType.FAILED, str(e))

        return Result(ResultType.COMPLETED)

    def get_application_timeout(self) -> int:
        """Return application timeout in seconds."""
        return CINDER_VOLUME_APP_TIMEOUT  # 20 minutes, same as cinder-volume
