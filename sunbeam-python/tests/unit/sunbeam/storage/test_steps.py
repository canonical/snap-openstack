# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from typing import Annotated
from unittest.mock import Mock, patch

import pydantic
import pytest

from sunbeam.core.questions import PasswordPromptQuestion, PromptQuestion
from sunbeam.storage.models import SecretDictField
from sunbeam.storage.steps import (
    DeploySpecificCinderVolumeStep,
    basemodel_validator,
    generate_questions_from_config,
)


class SampleConfig(pydantic.BaseModel):
    required_field: Annotated[
        int,
        pydantic.Field(ge=1, description="A positive integer"),
    ]
    secret_field: Annotated[
        str,
        pydantic.Field(description="A secret value"),
        SecretDictField(field="secret"),
    ]
    optional_field: Annotated[
        int | None,
        pydantic.Field(ge=0, description="Optional value"),
    ] = None

    @pydantic.field_validator("secret_field")
    @classmethod
    def no_digits(cls, value: str) -> str:
        if any(ch.isdigit() for ch in value):
            raise ValueError("must not contain digits")
        return value

    @pydantic.model_validator(mode="after")
    def disallow_thirteen(self):
        if getattr(self, "required_field", None) == 13:
            raise ValueError("thirteen is not allowed")
        return self


class TestBasemodelValidator:
    def test_valid_and_invalid_values(self):
        field_validator = basemodel_validator(SampleConfig)

        # Valid value should pass without raising
        field_validator("required_field")(10)

        # Root validator error should be surfaced as ValueError
        with pytest.raises(ValueError, match="thirteen is not allowed"):
            field_validator("required_field")(13)

        # Field-level validation should be applied
        with pytest.raises(ValueError, match="must not contain digits"):
            field_validator("secret_field")("password1")

        # Type enforcement should be handled by pydantic
        with pytest.raises(ValueError):
            field_validator("required_field")("not-an-int")

    def test_unknown_field_raises_value_error(self):
        field_validator = basemodel_validator(SampleConfig)
        with pytest.raises(ValueError, match="has no field named"):
            field_validator("missing")


class TestGenerateQuestionsFromConfig:
    def test_required_questions_include_validation(self):
        questions = generate_questions_from_config(SampleConfig)

        assert set(questions.keys()) == {"required_field", "secret_field"}
        assert all(
            isinstance(question, (PromptQuestion, PasswordPromptQuestion))
            for question in questions.values()
        )

        secret_question = questions["secret_field"]
        assert isinstance(secret_question, PasswordPromptQuestion)
        with pytest.raises(ValueError, match="must not contain digits"):
            secret_question.validation_function("password1")  # type: ignore[arg-type]

        required_question = questions["required_field"]
        assert required_question.validation_function is not None
        with pytest.raises(ValueError):
            required_question.validation_function("bad")  # type: ignore[arg-type]

    def test_optional_questions_include_validation(self):
        questions = generate_questions_from_config(SampleConfig, optional=True)

        assert set(questions.keys()) == {"optional_field"}
        optional_question = questions["optional_field"]
        assert optional_question.validation_function is not None
        optional_question.validation_function(5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            optional_question.validation_function(-1)  # type: ignore[arg-type]


class TestDeploySpecificCinderVolumeStep:
    """Tests for DeploySpecificCinderVolumeStep class."""

    @pytest.fixture
    def mock_backend_instance(self):
        """Mock storage backend instance."""
        backend = Mock()
        backend.principal_application = "cinder-volume-noha"
        backend.supports_ha = False
        backend.snap_name = "cinder-volume_noha"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @pytest.fixture
    def deploy_specific_cinder_volume_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mock_backend_instance,
    ):
        """Create DeploySpecificCinderVolumeStep instance for testing."""
        return DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            mock_backend_instance,
            test_model,
        )

    def test_init_without_extra_tfvars(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mock_backend_instance,
    ):
        """Test that extra_tfvars defaults to empty dict when not provided."""
        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            mock_backend_instance,
            test_model,
        )
        assert step.extra_tfvars == {}

    def test_init_with_extra_tfvars(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mock_backend_instance,
    ):
        """Test that extra_tfvars parameter is stored correctly."""
        extra_tfvars = {"enable-telemetry-notifications": True, "custom-key": "value"}
        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            mock_backend_instance,
            test_model,
            extra_tfvars=extra_tfvars,
        )
        assert step.extra_tfvars == extra_tfvars

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_run_applies_extra_tfvars(
        self,
        mock_get_offers,
        mock_read_config,
        deploy_specific_cinder_volume_step,
        basic_client,
        basic_deployment,
        mock_backend_instance,
    ):
        """Test that extra_tfvars are applied to terraform vars."""
        # Setup mocks
        mock_read_config.return_value = {"model": "test-uuid"}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        basic_client.cluster.list_nodes_by_role.return_value = [{"machineid": "1"}]

        # Mock jhelper
        deploy_specific_cinder_volume_step.jhelper.get_model.return_value = {
            "model-uuid": "test-uuid"
        }
        deploy_specific_cinder_volume_step.jhelper.wait_application_ready = Mock()

        # Mock deployment methods
        basic_deployment.get_space.return_value = "test-space"
        basic_deployment.get_tfhelper.return_value = Mock()

        # Mock feature manager to return telemetry disabled
        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        basic_deployment.get_feature_manager.return_value = feature_manager

        # Mock manifest
        mock_cinder_volume_charm = Mock()
        mock_cinder_volume_charm.config = {"test": "value"}
        mock_cinder_volume_charm.channel = "2024.1/edge"
        mock_cinder_volume_charm.revision = 123
        deploy_specific_cinder_volume_step.manifest.core.software.charms = {
            "cinder-volume": mock_cinder_volume_charm
        }

        # Mock tfhelper
        deploy_specific_cinder_volume_step.tfhelper.update_tfvars_and_apply_tf = Mock()

        # Set extra_tfvars with telemetry enabled (overriding feature manager)
        deploy_specific_cinder_volume_step.extra_tfvars = {
            "enable-telemetry-notifications": True
        }

        # Run the step
        deploy_specific_cinder_volume_step.run()

        # Verify tfhelper was called with extra_tfvars applied
        assert deploy_specific_cinder_volume_step.tfhelper.update_tfvars_and_apply_tf.called
        call_args = deploy_specific_cinder_volume_step.tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]

        # Verify that extra_tfvars override took precedence
        assert (
            tfvars["cinder-volumes"]["cinder-volume-noha"][
                "enable-telemetry-notifications"
            ]
            is True
        )

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_run_extra_tfvars_precedence(
        self,
        mock_get_offers,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mock_backend_instance,
    ):
        """Test that extra_tfvars values take precedence over defaults."""
        # Setup mocks
        mock_read_config.return_value = {"model": "test-uuid"}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        basic_client.cluster.list_nodes_by_role.return_value = [{"machineid": "1"}]

        # Mock jhelper
        basic_jhelper.get_model.return_value = {"model-uuid": "test-uuid"}
        basic_jhelper.wait_application_ready = Mock()

        # Mock deployment methods
        basic_deployment.get_space.return_value = "test-space"
        basic_deployment.get_tfhelper.return_value = Mock()

        # Mock feature manager to return telemetry DISABLED
        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        basic_deployment.get_feature_manager.return_value = feature_manager

        # Mock manifest
        mock_cinder_volume_charm = Mock()
        mock_cinder_volume_charm.config = {}
        mock_cinder_volume_charm.channel = "2024.1/edge"
        mock_cinder_volume_charm.revision = 123
        basic_manifest.core.software.charms = {
            "cinder-volume": mock_cinder_volume_charm
        }

        # Mock tfhelper
        basic_tfhelper.update_tfvars_and_apply_tf = Mock()

        # Create step with extra_tfvars explicitly enabling telemetry
        # (should override the feature manager which says it's disabled)
        extra_tfvars = {"enable-telemetry-notifications": True}
        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            mock_backend_instance,
            test_model,
            extra_tfvars=extra_tfvars,
        )

        # Run the step
        step.run()

        # Verify tfhelper was called
        assert basic_tfhelper.update_tfvars_and_apply_tf.called
        call_args = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]

        # Verify that extra_tfvars took precedence
        # Feature manager says False, but extra_tfvars says True -> should be True
        assert (
            tfvars["cinder-volumes"]["cinder-volume-noha"][
                "enable-telemetry-notifications"
            ]
            is True
        )
