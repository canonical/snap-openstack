# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from typing import Annotated
from unittest.mock import Mock, patch

import pydantic
import pytest

from sunbeam.clusterd.service import (
    ConfigItemNotFoundException,
    NodeNotExistInClusterException,
)
from sunbeam.core.common import ResultType
from sunbeam.core.juju import ApplicationNotFoundException
from sunbeam.core.questions import PasswordPromptQuestion, PromptQuestion
from sunbeam.storage.models import SecretDictField
from sunbeam.storage.steps import (
    BaseStorageBackendDestroyStep,
    CheckStorageNodeRemovalStep,
    DeploySpecificCinderVolumeStep,
    DestroySpecificCinderVolumeStep,
    RemoveStorageMachineUnitsStep,
    ValidateStoragePrerequisitesStep,
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
    @patch("sunbeam.storage.steps.get_optional_control_plane_offers")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_run_applies_extra_tfvars(
        self,
        mock_get_offers,
        mock_get_optional_offers,
        mock_read_config,
        deploy_specific_cinder_volume_step,
        basic_client,
        basic_deployment,
        mock_backend_instance,
        step_context,
    ):
        """Test that extra_tfvars are applied to terraform vars."""
        # Setup mocks
        mock_read_config.return_value = {"model": "test-uuid"}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        mock_get_optional_offers.return_value = {
            "cert-distributor-offer-url": "cert-distributor-url",
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
        deploy_specific_cinder_volume_step.run(step_context)

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
        assert (
            tfvars["cinder-volumes"]["cinder-volume-noha"]["cert-distributor-offer-url"]
            == "cert-distributor-url"
        )
        assert any(
            binding.get("endpoint") == "receive-ca-cert"
            for binding in tfvars["cinder-volumes"]["cinder-volume-noha"][
                "endpoint_bindings"
            ]
        )

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_optional_control_plane_offers")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_run_extra_tfvars_precedence(
        self,
        mock_get_offers,
        mock_get_optional_offers,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        mock_backend_instance,
        step_context,
    ):
        """Test that extra_tfvars values take precedence over defaults."""
        # Setup mocks
        mock_read_config.return_value = {"model": "test-uuid"}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        mock_get_optional_offers.return_value = {
            "cert-distributor-offer-url": "cert-distributor-url",
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
        step.run(step_context)

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


class TestDeploySpecificCinderVolumeStepIsSkip:
    """Tests for DeploySpecificCinderVolumeStep.is_skip() lifecycle logic."""

    @pytest.fixture
    def ha_backend_instance(self):
        """Mock HA storage backend instance."""
        backend = Mock()
        backend.principal_application = "cinder-volume"
        backend.supports_ha = True
        backend.snap_name = "cinder-volume"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @pytest.fixture
    def noha_backend_instance(self):
        """Mock non-HA storage backend instance."""
        backend = Mock()
        backend.principal_application = "cinder-volume-noha"
        backend.supports_ha = False
        backend.snap_name = "cinder-volume_noha"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @patch("sunbeam.storage.steps.read_config")
    def test_ha_backend_does_not_skip_when_no_cinder_volume_entry(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """HA backend should NOT skip deploy when no cinder-volume entry exists."""
        basic_client.cluster.list_nodes_by_role.return_value = [{"machineid": "0"}]
        mock_read_config.return_value = {"backends": {}, "cinder-volumes": {}}

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.storage.steps.read_config")
    def test_ha_backend_does_not_skip_when_config_not_found(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """HA backend should NOT skip deploy when config key doesn't exist yet."""
        basic_client.cluster.list_nodes_by_role.return_value = [{"machineid": "0"}]
        mock_read_config.side_effect = ConfigItemNotFoundException("not found")

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_ha_backend_does_not_skip_when_cinder_volume_entry_exists(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """HA backend must not skip when a principal entry already exists.

        Regression test: previously the step skipped, which prevented
        machine_ids from being refreshed when a new storage node joined.
        """
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
        ]

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.storage.steps.read_config")
    def test_noha_backend_does_not_skip_when_no_cinder_volume_entry(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        noha_backend_instance,
        step_context,
    ):
        """Non-HA backend should NOT skip when no cinder-volume entry exists."""
        basic_client.cluster.list_nodes_by_role.return_value = [{"machineid": "0"}]
        mock_read_config.return_value = {"backends": {}, "cinder-volumes": {}}

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            noha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_deploy_fails_when_no_storage_nodes(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Deploy should fail when no storage nodes are found."""
        basic_client.cluster.list_nodes_by_role.return_value = []

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED


class TestDeploySpecificCinderVolumeStepRunMachineIds:
    """Tests for DeploySpecificCinderVolumeStep.run() machine ID selection."""

    @pytest.fixture
    def ha_backend_instance(self):
        backend = Mock()
        backend.principal_application = "cinder-volume"
        backend.supports_ha = True
        backend.snap_name = "cinder-volume"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @pytest.fixture
    def noha_backend_instance(self):
        backend = Mock()
        backend.principal_application = "cinder-volume-noha"
        backend.supports_ha = False
        backend.snap_name = "cinder-volume_noha"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_optional_control_plane_offers")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_ha_deploy_uses_all_storage_node_machine_ids(
        self,
        mock_get_offers,
        mock_get_optional_offers,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """HA deploy should use all storage node machine IDs."""
        mock_read_config.return_value = {}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        mock_get_optional_offers.return_value = {}
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
            {"machineid": "2"},
        ]
        basic_jhelper.get_model.return_value = {"model-uuid": "test-uuid"}
        basic_jhelper.wait_application_ready = Mock()
        basic_deployment.get_space.return_value = "test-space"
        basic_deployment.get_tfhelper.return_value = Mock()

        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        basic_deployment.get_feature_manager.return_value = feature_manager

        mock_charm = Mock()
        mock_charm.config = {}
        mock_charm.channel = "2024.1/edge"
        mock_charm.revision = 123
        basic_manifest.core.software.charms = {"cinder-volume": mock_charm}

        basic_tfhelper.update_tfvars_and_apply_tf = Mock()

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        step.run(step_context)

        call_args = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]
        machine_ids = tfvars["cinder-volumes"]["cinder-volume"]["machine_ids"]
        assert machine_ids == ["0", "1", "2"]

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_optional_control_plane_offers")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_noha_deploy_uses_first_node_only(
        self,
        mock_get_offers,
        mock_get_optional_offers,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        noha_backend_instance,
        step_context,
    ):
        """Non-HA deploy should use first storage node only."""
        mock_read_config.return_value = {}
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        mock_get_optional_offers.return_value = {}
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
            {"machineid": "2"},
        ]
        basic_jhelper.get_model.return_value = {"model-uuid": "test-uuid"}
        basic_jhelper.wait_application_ready = Mock()
        basic_deployment.get_space.return_value = "test-space"
        basic_deployment.get_tfhelper.return_value = Mock()

        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        basic_deployment.get_feature_manager.return_value = feature_manager

        mock_charm = Mock()
        mock_charm.config = {}
        mock_charm.channel = "2024.1/edge"
        mock_charm.revision = 123
        basic_manifest.core.software.charms = {"cinder-volume": mock_charm}

        basic_tfhelper.update_tfvars_and_apply_tf = Mock()

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            noha_backend_instance,
            test_model,
        )

        step.run(step_context)

        call_args = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]
        machine_ids = tfvars["cinder-volumes"]["cinder-volume-noha"]["machine_ids"]
        assert machine_ids == ["0"]

    @patch("sunbeam.storage.steps.read_config")
    @patch("sunbeam.storage.steps.get_optional_control_plane_offers")
    @patch("sunbeam.storage.steps.get_mandatory_control_plane_offers")
    def test_ha_deploy_refreshes_machine_ids_on_scale_out(
        self,
        mock_get_offers,
        mock_get_optional_offers,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Existing stale machine_ids must be overwritten on scale-out.

        Regression: the step previously skipped when a principal entry
        existed, so a newly joined storage node never got a unit.
        """
        mock_read_config.return_value = {
            "model": "test-uuid",
            "cinder-volumes": {
                "cinder-volume": {
                    "application_name": "cinder-volume",
                    "machine_ids": ["0"],
                }
            },
        }
        mock_get_offers.return_value = {
            "keystone-offer-url": "keystone-url",
            "amqp-offer-url": "amqp-url",
            "database-offer-url": "database-url",
        }
        mock_get_optional_offers.return_value = {}
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
            {"machineid": "2"},
        ]
        basic_jhelper.get_model.return_value = {"model-uuid": "test-uuid"}
        basic_jhelper.wait_application_ready = Mock()
        basic_deployment.get_space.return_value = "test-space"
        basic_deployment.get_tfhelper.return_value = Mock()

        feature_manager = Mock()
        feature_manager.is_feature_enabled.return_value = False
        basic_deployment.get_feature_manager.return_value = feature_manager

        mock_charm = Mock()
        mock_charm.config = {}
        mock_charm.channel = "2024.1/edge"
        mock_charm.revision = 123
        basic_manifest.core.software.charms = {"cinder-volume": mock_charm}

        basic_tfhelper.update_tfvars_and_apply_tf = Mock()

        step = DeploySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "test-backend",
            ha_backend_instance,
            test_model,
        )

        step.run(step_context)

        call_args = basic_tfhelper.update_tfvars_and_apply_tf.call_args
        tfvars = call_args[1]["override_tfvars"]
        machine_ids = tfvars["cinder-volumes"]["cinder-volume"]["machine_ids"]
        assert machine_ids == ["0", "1", "2"]


class TestDestroySpecificCinderVolumeStepIsSkip:
    """Tests for DestroySpecificCinderVolumeStep.is_skip() lifecycle logic."""

    @pytest.fixture
    def ha_backend_instance(self):
        backend = Mock()
        backend.principal_application = "cinder-volume"
        backend.supports_ha = True
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @pytest.fixture
    def noha_backend_instance(self):
        backend = Mock()
        backend.principal_application = "cinder-volume-noha"
        backend.supports_ha = False
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        return backend

    @patch("sunbeam.storage.steps.read_config")
    def test_destroy_skips_when_another_ha_backend_uses_same_principal(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Destroy should skip when another backend still uses the same principal."""
        mock_read_config.return_value = {
            "backends": {
                "backend-a": {"principal_application": "cinder-volume"},
                "backend-b": {"principal_application": "cinder-volume"},
            },
            "cinder-volumes": {"cinder-volume": {"application_name": "cinder-volume"}},
        }

        step = DestroySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-a",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.storage.steps.read_config")
    def test_destroy_proceeds_when_no_other_backend_uses_principal(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Destroy should proceed when no other backend uses the principal."""
        mock_read_config.return_value = {
            "backends": {
                "backend-a": {"principal_application": "cinder-volume"},
            },
            "cinder-volumes": {"cinder-volume": {"application_name": "cinder-volume"}},
        }

        step = DestroySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-a",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    @patch("sunbeam.storage.steps.read_config")
    def test_destroy_skips_when_principal_entry_not_in_cinder_volumes(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Destroy should skip when principal entry doesn't exist in tfvars."""
        mock_read_config.return_value = {
            "backends": {
                "backend-a": {"principal_application": "cinder-volume"},
            },
            "cinder-volumes": {},
        }

        step = DestroySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-a",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.storage.steps.read_config")
    def test_destroy_skips_when_config_not_found(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        ha_backend_instance,
        step_context,
    ):
        """Destroy should skip when config doesn't exist (nothing deployed)."""
        mock_read_config.side_effect = ConfigItemNotFoundException("not found")

        step = DestroySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-a",
            ha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    @patch("sunbeam.storage.steps.read_config")
    def test_destroy_noha_proceeds_when_only_backend(
        self,
        mock_read_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        test_model,
        noha_backend_instance,
        step_context,
    ):
        """Non-HA destroy should proceed when it is the only backend."""
        mock_read_config.return_value = {
            "backends": {
                "backend-noha": {"principal_application": "cinder-volume-noha"},
            },
            "cinder-volumes": {
                "cinder-volume-noha": {"application_name": "cinder-volume-noha"}
            },
        }

        step = DestroySpecificCinderVolumeStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-noha",
            noha_backend_instance,
            test_model,
        )

        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED


class TestBaseStorageBackendDestroyStepRun:
    """Tests for BaseStorageBackendDestroyStep.run() idempotency."""

    @pytest.fixture
    def backend_instance(self):
        backend = Mock()
        backend.display_name = "Test Backend"
        backend.tfvar_config_key = "TerraformVarsStorageBackends"
        backend.config_key = Mock(return_value="Storage-backend-a")
        return backend

    def _make_step(
        self,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        backend_instance,
        test_model,
    ):
        return BaseStorageBackendDestroyStep(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            "backend-a",
            backend_instance,
            test_model,
        )

    @patch("sunbeam.storage.steps.update_config")
    @patch("sunbeam.storage.steps.read_config")
    def test_run_does_not_raise_when_tfvars_config_missing(
        self,
        mock_read_config,
        mock_update_config,
        basic_deployment,
        basic_client,
        basic_tfhelper,
        basic_jhelper,
        basic_manifest,
        backend_instance,
        test_model,
        step_context,
    ):
        """Regression test for KeyError when tfvars is {} from a missing config.

        Previously the destroy step indexed tfvars['backends'] directly,
        raising KeyError when ConfigItemNotFoundException set tfvars = {}.
        The step must now tolerate a missing config item and return
        COMPLETED after a clean terraform apply.
        """
        mock_read_config.side_effect = ConfigItemNotFoundException("not found")
        basic_tfhelper.update_tfvars_and_apply_tf.return_value = None
        basic_client.cluster.delete_storage_backend = Mock()
        basic_client.cluster.delete_config = Mock()

        step = self._make_step(
            basic_deployment,
            basic_client,
            basic_tfhelper,
            basic_jhelper,
            basic_manifest,
            backend_instance,
            test_model,
        )
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        assert basic_tfhelper.update_tfvars_and_apply_tf.called
        applied_tfvars = basic_tfhelper.update_tfvars_and_apply_tf.call_args[1][
            "override_tfvars"
        ]
        assert applied_tfvars.get("backends") == {}


class TestValidateStoragePrerequisitesStep:
    """Tests for ValidateStoragePrerequisitesStep."""

    @pytest.fixture
    def validate_step(self, basic_deployment, basic_client, basic_jhelper):
        """Create ValidateStoragePrerequisitesStep instance for testing."""
        basic_deployment.openstack_machines_model = "openstack-machines"
        return ValidateStoragePrerequisitesStep(
            basic_deployment,
            basic_client,
            basic_jhelper,
        )

    def test_succeeds_without_cinder_volume_app(
        self,
        validate_step,
        basic_client,
        basic_jhelper,
        step_context,
    ):
        """Step should succeed when cinder-volume app does not exist.

        As long as Juju auth, bootstrap, model, and storage nodes are OK,
        the absence of a cinder-volume application must not cause failure.
        """
        # Juju auth succeeds
        basic_jhelper.models.return_value = ["openstack-machines"]

        # Sunbeam bootstrapped
        basic_client.cluster.check_sunbeam_bootstrapped.return_value = True

        # OpenStack model exists
        basic_jhelper.model_exists.return_value = True

        # Storage nodes are deployed
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"machineid": "0"},
            {"machineid": "1"},
        ]

        # No cinder-volume application present (this must NOT cause failure)
        basic_jhelper.get_application.side_effect = Exception(
            "application cinder-volume not found"
        )

        result = validate_step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_fails_when_not_bootstrapped(
        self,
        validate_step,
        basic_client,
        basic_jhelper,
        step_context,
    ):
        """Step should fail when Sunbeam is not bootstrapped."""
        basic_jhelper.models.return_value = ["openstack-machines"]
        basic_client.cluster.check_sunbeam_bootstrapped.return_value = False

        result = validate_step.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_fails_when_no_storage_nodes(
        self,
        validate_step,
        basic_client,
        basic_jhelper,
        step_context,
    ):
        """Step should fail when no storage nodes exist."""
        basic_jhelper.models.return_value = ["openstack-machines"]
        basic_client.cluster.check_sunbeam_bootstrapped.return_value = True
        basic_jhelper.model_exists.return_value = True
        basic_client.cluster.list_nodes_by_role.return_value = []

        result = validate_step.run(step_context)
        assert result.result_type == ResultType.FAILED

    def test_fails_when_model_does_not_exist(
        self,
        validate_step,
        basic_client,
        basic_jhelper,
        step_context,
    ):
        """Step should fail when OpenStack model does not exist."""
        basic_jhelper.models.return_value = ["openstack-machines"]
        basic_client.cluster.check_sunbeam_bootstrapped.return_value = True
        basic_jhelper.model_exists.return_value = False

        result = validate_step.run(step_context)
        assert result.result_type == ResultType.FAILED


class TestCheckStorageNodeRemovalStep:
    """Tests for CheckStorageNodeRemovalStep."""

    @pytest.fixture
    def make_step(self, basic_client, basic_jhelper):
        """Factory for creating CheckStorageNodeRemovalStep with defaults."""

        def _make(force=False, node_name="node-0", model="openstack-machines"):
            return CheckStorageNodeRemovalStep(
                basic_client, node_name, basic_jhelper, model, force=force
            )

        return _make

    def test_skips_for_non_storage_node(self, make_step, basic_client, step_context):
        """Step should skip when the departing node is not a storage node."""
        basic_client.cluster.get_node_info.return_value = {
            "name": "node-0",
            "role": "control",
            "machineid": "0",
        }

        step = make_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_skips_when_node_not_found(self, make_step, basic_client, step_context):
        """Step should skip when the node does not exist in the cluster."""
        basic_client.cluster.get_node_info.side_effect = NodeNotExistInClusterException(
            "not found"
        )

        step = make_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_skips_when_cinder_volume_not_deployed(
        self, make_step, basic_client, basic_jhelper, step_context
    ):
        """Step should skip when cinder-volume app does not exist."""
        basic_client.cluster.get_node_info.return_value = {
            "name": "node-0",
            "role": "storage",
            "machineid": "0",
        }
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = make_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_skips_when_no_cinder_volume_unit_on_node(
        self, make_step, basic_client, basic_jhelper, step_context
    ):
        """Step should skip when the node has no cinder-volume units."""
        basic_client.cluster.get_node_info.return_value = {
            "name": "node-0",
            "role": "storage",
            "machineid": "0",
        }

        # cinder-volume app exists but units are on different machines
        mock_app = Mock()
        mock_unit = Mock()
        mock_unit.machine = "99"
        mock_app.units = {"cinder-volume/0": mock_unit}
        basic_jhelper.get_application.return_value = mock_app

        step = make_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_proceeds_when_node_hosts_cinder_volume(
        self, make_step, basic_client, basic_jhelper, step_context
    ):
        """Step should NOT skip when the node hosts a cinder-volume unit."""
        basic_client.cluster.get_node_info.return_value = {
            "name": "node-0",
            "role": "storage",
            "machineid": "0",
        }

        mock_app = Mock()
        mock_unit = Mock()
        mock_unit.machine = "0"
        mock_app.units = {"cinder-volume/0": mock_unit}
        basic_jhelper.get_application.return_value = mock_app

        step = make_step()
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_fails_when_last_storage_node_without_force(
        self, make_step, basic_client, step_context
    ):
        """Removing the last storage node should fail without --force."""
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"name": "node-0", "machineid": "0"}
        ]

        step = make_step(force=False)
        result = step.run(step_context)
        assert result.result_type == ResultType.FAILED
        assert "Cannot remove the last storage node" in result.message

    def test_succeeds_with_force_on_last_node(
        self, make_step, basic_client, step_context
    ):
        """Removing the last storage node should succeed with --force."""
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"name": "node-0", "machineid": "0"}
        ]

        step = make_step(force=True)
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_succeeds_when_multiple_storage_nodes(
        self, make_step, basic_client, step_context
    ):
        """Removing a storage node should succeed when others remain."""
        basic_client.cluster.list_nodes_by_role.return_value = [
            {"name": "node-0", "machineid": "0"},
            {"name": "node-1", "machineid": "1"},
        ]

        step = make_step(force=False)
        result = step.run(step_context)
        assert result.result_type == ResultType.COMPLETED


class TestRemoveStorageMachineUnitsStep:
    """Tests for RemoveStorageMachineUnitsStep."""

    def test_inherits_remove_machine_units_step(self):
        """Step should inherit from RemoveMachineUnitsStep."""
        from sunbeam.core.steps import RemoveMachineUnitsStep

        assert issubclass(RemoveStorageMachineUnitsStep, RemoveMachineUnitsStep)

    def test_constructor_sets_application(self, basic_client, basic_jhelper):
        """Step should target cinder-volume application."""
        step = RemoveStorageMachineUnitsStep(
            basic_client, "node-0", basic_jhelper, "openstack-machines"
        )
        assert step.application == "cinder-volume"

    def test_unit_timeout(self, basic_client, basic_jhelper):
        """Step should use 30-minute timeout."""
        step = RemoveStorageMachineUnitsStep(
            basic_client, "node-0", basic_jhelper, "openstack-machines"
        )
        assert step.get_unit_timeout() == 1800

    def test_skips_when_cinder_volume_not_deployed(
        self, basic_client, basic_jhelper, step_context
    ):
        """Step should skip when cinder-volume application does not exist."""
        basic_client.cluster.list_nodes.return_value = [
            {"name": "node-0", "machineid": "0"}
        ]
        basic_jhelper.get_application.side_effect = ApplicationNotFoundException(
            "not found"
        )

        step = RemoveStorageMachineUnitsStep(
            basic_client, "node-0", basic_jhelper, "openstack-machines"
        )
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED
