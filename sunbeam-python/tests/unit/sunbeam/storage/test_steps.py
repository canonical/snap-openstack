# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock, patch

from sunbeam.core.common import ResultType
from sunbeam.core.deployment import Deployment
from sunbeam.storage.basestorage import StorageBackendConfig
from sunbeam.storage.steps import (
    CheckBackendExistsStep,
    DeployCharmStep,
    IntegrateWithCinderVolumeStep,
    RemoveBackendStep,
    ValidateBackendExistsStep,
    ValidateConfigStep,
    WaitForReadyStep,
)


class TestValidateConfigStep(unittest.TestCase):
    """Test cases for ValidateConfigStep."""

    def test_init(self):
        """Test step initialization."""
        config = StorageBackendConfig(name="test-backend")
        step = ValidateConfigStep(config)

        self.assertEqual(step.config, config)
        self.assertEqual(step.name, "Validate Configuration")
        self.assertIn("test-backend", step.description)

    def test_run_success(self):
        """Test successful validation run."""
        config = StorageBackendConfig(name="test-backend")
        step = ValidateConfigStep(config)

        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        # ValidateConfigStep doesn't log debug messages, just validates config

    def test_is_skip_false(self):
        """Test that step is not skipped by default."""
        config = StorageBackendConfig(name="test-backend")
        step = ValidateConfigStep(config)

        result = step.is_skip()
        # is_skip() returns a Result object, check if it indicates not skipped
        if hasattr(result, "result_type"):
            self.assertEqual(result.result_type, ResultType.COMPLETED)
        else:
            self.assertFalse(result)

    def test_has_prompts_false(self):
        """Test that step has no prompts by default."""
        config = StorageBackendConfig(name="test-backend")
        step = ValidateConfigStep(config)

        self.assertFalse(step.has_prompts())


class TestCheckBackendExistsStep(unittest.TestCase):
    """Test cases for CheckBackendExistsStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.config = StorageBackendConfig(name="test-backend")

    def test_init(self):
        """Test step initialization."""
        step = CheckBackendExistsStep(self.deployment, "test-backend")

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.backend_name, "test-backend")
        self.assertEqual(step.name, "Check Backend Exists")
        self.assertIn("test-backend", step.description)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_backend_exists(self, mock_service_class):
        """Test run when backend already exists."""
        mock_service = Mock()
        mock_service.backend_exists.return_value = True
        mock_service_class.return_value = mock_service

        step = CheckBackendExistsStep(self.deployment, "test-backend")
        result = step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.assertIn("already exists", result.message)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_service.backend_exists.assert_called_once_with("test-backend")

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_backend_not_exists(self, mock_service_class):
        """Test run when backend doesn't exist."""
        mock_service = Mock()
        mock_service.backend_exists.return_value = False
        mock_service_class.return_value = mock_service

        step = CheckBackendExistsStep(self.deployment, "test-backend")
        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_service.backend_exists.assert_called_once_with("test-backend")


class TestValidateBackendExistsStep(unittest.TestCase):
    """Test cases for ValidateBackendExistsStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.backend_name = "test-backend"

    def test_init(self):
        """Test step initialization."""
        step = ValidateBackendExistsStep(self.deployment, self.backend_name)

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.backend_name, self.backend_name)
        self.assertEqual(step.name, "Validate Backend Exists")
        self.assertIn("test-backend", step.description)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_backend_exists(self, mock_service_class):
        """Test run when backend exists."""
        mock_service = Mock()
        mock_service.backend_exists.return_value = True
        mock_service_class.return_value = mock_service

        step = ValidateBackendExistsStep(self.deployment, self.backend_name)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_service.backend_exists.assert_called_once_with(self.backend_name)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_backend_not_exists(self, mock_service_class):
        """Test run when backend doesn't exist."""
        mock_service = Mock()
        mock_service.backend_exists.return_value = False
        mock_service_class.return_value = mock_service

        step = ValidateBackendExistsStep(self.deployment, self.backend_name)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.assertIn("not found", result.message)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_service.backend_exists.assert_called_once_with(self.backend_name)


class TestDeployCharmStep(unittest.TestCase):
    """Test cases for DeployCharmStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.config = StorageBackendConfig(name="test-backend")
        self.charm_name = "test-charm"
        self.charm_config = {"key": "value"}

    def test_init(self):
        """Test step initialization."""
        step = DeployCharmStep(
            self.deployment, self.config, self.charm_name, self.charm_config, ""
        )

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.config, self.config)
        self.assertEqual(step.charm_name, self.charm_name)
        self.assertEqual(step.charm_config, self.charm_config)
        self.assertEqual(step.name, "Deploy Charm")
        self.assertIn("test-backend", step.description)
        self.assertIn("test-charm", step.description)

    def test_init_with_local_charm(self):
        """Test step initialization with local charm."""
        local_charm = "/path/to/charm"
        step = DeployCharmStep(
            self.deployment,
            self.config,
            self.charm_name,
            self.charm_config,
            local_charm,
        )

        self.assertEqual(step.local_charm_path, local_charm)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_success(self, mock_service_class):
        """Test successful charm deployment."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        step = DeployCharmStep(
            self.deployment,
            self.config,
            self.charm_name,
            self.charm_config,
            "",  # local_charm_path
        )

        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_juju_helper.deploy.assert_called_once_with(
            self.config.name,
            self.charm_name,
            mock_service.model,
            config=self.charm_config,
            trust=False,
        )

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_with_local_charm(self, mock_service_class):
        """Test charm deployment with local charm."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        local_charm = "/path/to/charm"
        step = DeployCharmStep(
            self.deployment,
            self.config,
            self.charm_name,
            self.charm_config,
            local_charm,
        )

        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_juju_helper.deploy.assert_called_once_with(
            self.config.name,
            local_charm,  # Should use local charm path
            mock_service.model,
            config=self.charm_config,
            trust=True,  # Should be True for local charms
        )


class TestIntegrateWithCinderVolumeStep(unittest.TestCase):
    """Test cases for IntegrateWithCinderVolumeStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.config = StorageBackendConfig(name="test-backend")

    def test_init(self):
        """Test step initialization."""
        step = IntegrateWithCinderVolumeStep(self.deployment, self.config)

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.config, self.config)
        self.assertEqual(step.name, "Integrate with Cinder Volume App")
        self.assertIn("test-backend", step.description)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_success(self, mock_service_class):
        """Test successful integration."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        step = IntegrateWithCinderVolumeStep(self.deployment, self.config)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_juju_helper.integrate.assert_called_once_with(
            mock_service.model, self.config.name, "cinder-volume", "cinder-volume"
        )

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_failure(self, mock_service_class):
        """Test integration failure."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        # Simulate integration failure
        mock_juju_helper.integrate.side_effect = Exception("Integration failed")

        step = IntegrateWithCinderVolumeStep(self.deployment, self.config)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.assertIn("Integration failed", result.message)


class TestWaitForReadyStep(unittest.TestCase):
    """Test cases for WaitForReadyStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.config = StorageBackendConfig(name="test-backend")

    def test_init_default_timeout(self):
        """Test step initialization with default timeout."""
        step = WaitForReadyStep(self.deployment, self.config)

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.config, self.config)
        self.assertEqual(step.timeout, 600)  # Default timeout
        self.assertEqual(step.name, "Wait for Ready")
        self.assertIn("test-backend", step.description)

    def test_init_custom_timeout(self):
        """Test step initialization with custom timeout."""
        step = WaitForReadyStep(self.deployment, self.config, timeout=600)

        self.assertEqual(step.timeout, 600)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_success(self, mock_service_class):
        """Test successful wait for ready."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        step = WaitForReadyStep(self.deployment, self.config)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_juju_helper.wait_application_ready.assert_called_once_with(
            self.config.name, model=mock_service.model, timeout=600
        )

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_timeout(self, mock_service_class):
        """Test timeout while waiting for ready."""
        mock_service = Mock()
        mock_juju_helper = Mock()
        mock_service.juju_helper = mock_juju_helper
        mock_service.model = "test-model"
        mock_service_class.return_value = mock_service

        # Simulate timeout by raising an exception
        mock_juju_helper.wait_application_ready.side_effect = Exception(
            "Application timed out"
        )

        step = WaitForReadyStep(self.deployment, self.config, timeout=1)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.assertIn("timed out", result.message)


class TestRemoveBackendStep(unittest.TestCase):
    """Test cases for RemoveBackendStep."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Add required attributes for StorageBackendService
        self.deployment.juju_controller = Mock()
        self.deployment.juju_controller.model = "test-model"
        self.backend_name = "test-backend"

    def test_init(self):
        """Test step initialization."""
        step = RemoveBackendStep(self.deployment, self.backend_name)

        self.assertEqual(step.deployment, self.deployment)
        self.assertEqual(step.backend_name, self.backend_name)
        self.assertEqual(step.name, "Remove Backend")
        self.assertIn("test-backend", step.description)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_success(self, mock_service_class):
        """Test successful backend removal."""
        mock_service = Mock()
        mock_service._is_storage_backend.return_value = True
        mock_service_class.return_value = mock_service

        step = RemoveBackendStep(self.deployment, self.backend_name)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.COMPLETED)
        mock_service_class.assert_called_once_with(self.deployment)
        mock_service._is_storage_backend.assert_called_once_with(self.backend_name)
        mock_service.remove_backend.assert_called_once_with(self.backend_name)

    @patch("sunbeam.storage.steps.StorageBackendService")
    def test_run_failure(self, mock_service_class):
        """Test backend removal failure."""
        mock_service = Mock()
        mock_service._is_storage_backend.return_value = True
        mock_service.remove_backend.side_effect = Exception("Removal failed")
        mock_service_class.return_value = mock_service

        step = RemoveBackendStep(self.deployment, self.backend_name)
        result = step.run()

        self.assertEqual(result.result_type, ResultType.FAILED)
        self.assertIn("Removal failed", result.message)


if __name__ == "__main__":
    unittest.main()
