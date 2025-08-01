# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock, PropertyMock, patch

try:
    from pydantic import ValidationError
except ImportError:
    # Fallback for environments without pydantic
    class ValidationError(Exception):
        pass


from sunbeam.core.deployment import Deployment
from sunbeam.core.juju import ApplicationNotFoundException, JujuException
from sunbeam.storage.basestorage import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    BackendValidationException,
    ExtendedJujuHelper,
    StorageBackendBase,
    StorageBackendConfig,
    StorageBackendException,
    StorageBackendInfo,
    StorageBackendService,
)


class TestExtendedJujuHelper(unittest.TestCase):
    """Test cases for ExtendedJujuHelper class."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Mock juju_controller as a property that returns a JujuController object
        mock_controller = Mock()
        mock_controller.name = "test-controller"
        type(self.deployment).juju_controller = PropertyMock(
            return_value=mock_controller
        )
        self.helper = ExtendedJujuHelper(self.deployment.juju_controller)

    @patch.object(ExtendedJujuHelper, "_model")
    def test_set_app_config_success(self, mock_model):
        """Test successful application configuration setting."""
        mock_juju = Mock()
        mock_model.return_value.__enter__.return_value = mock_juju

        config = {"key1": "value1", "key2": "value2"}
        self.helper.set_app_config("test-app", config, "test-model")

        mock_juju.config.assert_called_once_with("test-app", config)

    @patch.object(ExtendedJujuHelper, "_model")
    def test_set_app_config_app_not_found(self, mock_model):
        """Test setting config for non-existent application."""
        import jubilant

        mock_juju = Mock()
        mock_model.return_value.__enter__.return_value = mock_juju
        # Mock the actual exception type that the method expects
        cli_error = jubilant.CLIError("juju config", "")
        cli_error.stderr = "application not found"
        mock_juju.config.side_effect = cli_error

        with self.assertRaises(ApplicationNotFoundException):
            self.helper.set_app_config("missing-app", {}, "test-model")

    @patch.object(ExtendedJujuHelper, "_model")
    def test_set_app_config_juju_error(self, mock_model):
        """Test handling of general Juju errors."""
        from sunbeam.core.juju import JujuException

        mock_juju = Mock()
        mock_model.return_value.__enter__.return_value = mock_juju
        mock_juju.config.side_effect = JujuException("General Juju error")

        with self.assertRaises(JujuException):
            self.helper.set_app_config("test-app", {}, "test-model")

    @patch("subprocess.run")
    def test_reset_app_config_success(self, mock_run):
        """Test successful configuration reset."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        self.helper.reset_app_config("test-app", ["key1", "key2"], "test-model")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("juju", args)
        self.assertIn("config", args)
        self.assertIn("test-app", args)
        self.assertIn("--reset", args)

    @patch("subprocess.run")
    def test_reset_app_config_failure(self, mock_run):
        """Test configuration reset failure."""
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, "juju", stderr="error")

        with self.assertRaises(JujuException):
            self.helper.reset_app_config("test-app", ["key1"], "test-model")


class TestStorageBackendConfig(unittest.TestCase):
    """Test cases for StorageBackendConfig model."""

    def test_valid_config(self):
        """Test creating valid configuration."""
        config = StorageBackendConfig(name="test-backend")
        self.assertEqual(config.name, "test-backend")

    def test_invalid_config_missing_name(self):
        """Test validation error for missing name."""
        with self.assertRaises(ValidationError):
            StorageBackendConfig()


class TestStorageBackendInfo(unittest.TestCase):
    """Test cases for StorageBackendInfo model."""

    def test_valid_info(self):
        """Test creating valid backend info."""
        info = StorageBackendInfo(
            name="test-backend",
            backend_type="hitachi",
            status="active",
            charm="cinder-volume-hitachi",
            config={"key": "value"},
        )
        self.assertEqual(info.name, "test-backend")
        self.assertEqual(info.backend_type, "hitachi")
        self.assertEqual(info.status, "active")
        self.assertEqual(info.charm, "cinder-volume-hitachi")
        self.assertEqual(info.config, {"key": "value"})

    def test_info_with_defaults(self):
        """Test creating info with default values."""
        info = StorageBackendInfo(
            name="test-backend",
            backend_type="hitachi",
            status="active",
            charm="cinder-volume-hitachi",
        )
        self.assertEqual(info.config, {})


class TestStorageBackendService(unittest.TestCase):
    """Test cases for StorageBackendService class."""

    def setUp(self):
        self.deployment = Mock(spec=Deployment)
        # Mock juju_controller as a proper JujuController object
        mock_controller = Mock()
        mock_controller.name = "test-controller"
        type(self.deployment).juju_controller = PropertyMock(
            return_value=mock_controller
        )
        # Mock the property correctly
        type(self.deployment).openstack_machines_model = PropertyMock(
            return_value="test-model"
        )

        with patch.object(
            StorageBackendService, "_get_model_name", return_value="test-model"
        ):
            self.service = StorageBackendService(self.deployment)

    def test_init(self):
        """Test service initialization."""
        self.assertEqual(self.service.deployment, self.deployment)
        self.assertIsInstance(self.service.juju_helper, ExtendedJujuHelper)
        self.assertEqual(self.service.model, "test-model")

    def test_get_model_name(self):
        """Test model name retrieval."""
        with patch.object(
            StorageBackendService, "_get_model_name", return_value="openstack"
        ):
            service = StorageBackendService(self.deployment)
            self.assertEqual(service.model, "openstack")

    @patch.object(ExtendedJujuHelper, "get_application_names")
    def test_backend_exists_true(self, mock_get_apps):
        """Test backend existence check when backend exists."""
        mock_get_apps.return_value = ["test-backend", "other-app"]

        result = self.service.backend_exists("test-backend")
        self.assertTrue(result)

    @patch.object(ExtendedJujuHelper, "get_application_names")
    def test_backend_exists_false(self, mock_get_apps):
        """Test backend existence check when backend doesn't exist."""
        mock_get_apps.return_value = ["other-app"]

        result = self.service.backend_exists("test-backend")
        self.assertFalse(result)

    def test_normalize_charm_name(self):
        """Test charm name normalization."""
        test_cases = [
            ("local:cinder-volume-hitachi-123", "cinder-volume-hitachi"),
            ("cinder-volume-hitachi-456", "cinder-volume-hitachi"),
            ("cinder-volume-hitachi", "cinder-volume-hitachi"),
            ("local:some-charm", "some-charm"),
        ]

        for input_name, expected in test_cases:
            with self.subTest(input_name=input_name):
                result = self.service._normalize_charm_name(input_name)
                self.assertEqual(result, expected)

    @patch.object(ExtendedJujuHelper, "get_application_relations")
    def test_has_relation_to_cinder_volume_true(self, mock_get_relations):
        """Test relation check when relation exists."""
        mock_get_relations.return_value = [
            {"app": "cinder-volume", "endpoint": "storage-backend"}
        ]

        result = self.service._has_relation_to_cinder_volume("test-app")
        self.assertTrue(result)

    @patch.object(ExtendedJujuHelper, "get_application_relations")
    def test_has_relation_to_cinder_volume_false(self, mock_get_relations):
        """Test relation check when no relation exists."""
        mock_get_relations.return_value = [
            {"app": "other-app", "endpoint": "some-endpoint"}
        ]

        result = self.service._has_relation_to_cinder_volume("test-app")
        self.assertFalse(result)

    def test_get_backend_type_from_charm(self):
        """Test backend type detection from charm name."""
        test_cases = [
            ("cinder-volume-hitachi", "", "hitachi"),
            ("cinder-volume-ceph", "", "ceph"),
            ("cinder-volume-netapp", "", "netapp"),
            ("unknown-charm", "test-app", "test-app"),
            ("", "fallback-app", "fallback-app"),
        ]

        for charm_name, app_name, expected in test_cases:
            with self.subTest(charm_name=charm_name, app_name=app_name):
                result = self.service._get_backend_type_from_charm(charm_name, app_name)
                self.assertEqual(result, expected)

    @patch.object(StorageBackendService, "_has_relation_to_cinder_volume")
    def test_is_storage_backend_true(self, mock_has_relation):
        """Test storage backend identification when app is a backend."""
        mock_has_relation.return_value = True

        result = self.service._is_storage_backend("test-backend")
        self.assertTrue(result)

    @patch.object(StorageBackendService, "_has_relation_to_cinder_volume")
    def test_is_storage_backend_false(self, mock_has_relation):
        """Test storage backend identification when app is not a backend."""
        mock_has_relation.return_value = False

        result = self.service._is_storage_backend("test-app")
        self.assertFalse(result)

    @patch.object(ExtendedJujuHelper, "get_model_status_full")
    @patch.object(StorageBackendService, "_is_storage_backend")
    @patch.object(StorageBackendService, "_get_backend_type_from_charm")
    def test_list_backends(self, mock_get_type, mock_is_backend, mock_status):
        """Test listing storage backends."""
        mock_status.return_value = {
            "applications": {
                "test-backend": {
                    "charm": "cinder-volume-hitachi",
                    "application-status": {"current": "active"},
                },
                "cinder-volume": {
                    "charm": "cinder-volume",
                    "application-status": {"current": "active"},
                },
                "other-app": {
                    "charm": "some-charm",
                    "application-status": {"current": "active"},
                },
            }
        }
        mock_is_backend.side_effect = lambda x: x == "test-backend"
        mock_get_type.return_value = "hitachi"

        backends = self.service.list_backends()

        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0].name, "test-backend")
        self.assertEqual(backends[0].backend_type, "hitachi")
        self.assertEqual(backends[0].status, "active")
        self.assertEqual(backends[0].charm, "cinder-volume-hitachi")

    @patch.object(ExtendedJujuHelper, "get_app_config")
    @patch.object(StorageBackendService, "backend_exists")
    def test_get_backend_config_success(self, mock_exists, mock_get_config):
        """Test successful backend configuration retrieval."""
        mock_exists.return_value = True
        mock_get_config.return_value = {"key1": "value1", "key2": "value2"}

        config = self.service.get_backend_config("test-backend")

        self.assertEqual(config, {"key1": "value1", "key2": "value2"})
        mock_get_config.assert_called_once_with("test-backend", model="test-model")

    @patch.object(StorageBackendService, "backend_exists")
    def test_get_backend_config_not_found(self, mock_exists):
        """Test configuration retrieval for non-existent backend."""
        mock_exists.return_value = False

        with self.assertRaises(BackendNotFoundException):
            self.service.get_backend_config("missing-backend")

    @patch.object(ExtendedJujuHelper, "set_app_config")
    @patch.object(StorageBackendService, "backend_exists")
    def test_set_backend_config_success(self, mock_exists, mock_set_config):
        """Test successful backend configuration update."""
        mock_exists.return_value = True

        config_updates = {"key1": "new_value", "key2": "another_value"}
        self.service.set_backend_config("test-backend", config_updates)

        mock_set_config.assert_called_once_with(
            "test-backend", config_updates, model="test-model"
        )

    @patch.object(StorageBackendService, "backend_exists")
    def test_set_backend_config_not_found(self, mock_exists):
        """Test configuration update for non-existent backend."""
        mock_exists.return_value = False

        with self.assertRaises(BackendNotFoundException):
            self.service.set_backend_config("missing-backend", {"key": "value"})

    @patch.object(ExtendedJujuHelper, "reset_app_config")
    @patch.object(StorageBackendService, "backend_exists")
    def test_reset_backend_config_success(self, mock_exists, mock_reset_config):
        """Test successful backend configuration reset."""
        mock_exists.return_value = True

        keys = ["key1", "key2"]
        self.service.reset_backend_config("test-backend", keys)

        mock_reset_config.assert_called_once_with(
            "test-backend", keys, model="test-model"
        )

    @patch.object(StorageBackendService, "backend_exists")
    def test_reset_backend_config_not_found(self, mock_exists):
        """Test configuration reset for non-existent backend."""
        mock_exists.return_value = False

        with self.assertRaises(BackendNotFoundException):
            self.service.reset_backend_config("missing-backend", ["key1"])


class TestStorageBackendBase(unittest.TestCase):
    """Test cases for StorageBackendBase class."""

    def setUp(self):
        self.backend = StorageBackendBase()
        self.deployment = Mock(spec=Deployment)

    def test_init(self):
        """Test backend initialization."""
        self.assertEqual(self.backend.name, "base")
        self.assertEqual(self.backend.display_name, "Base Storage Backend")
        self.assertIsNone(self.backend.service)

    @patch.object(StorageBackendService, "__init__", return_value=None)
    def test_get_service(self, mock_service_init):
        """Test service creation and caching."""
        mock_service = Mock(spec=StorageBackendService)
        mock_service_init.return_value = None

        with patch(
            "sunbeam.storage.basestorage.StorageBackendService",
            return_value=mock_service,
        ):
            service1 = self.backend._get_service(self.deployment)
            service2 = self.backend._get_service(self.deployment)

            self.assertEqual(service1, mock_service)
            self.assertEqual(service1, service2)  # Should be cached

    def test_get_backend_type(self):
        """Test backend type extraction from app name."""
        test_cases = [
            ("cinder-volume-hitachi", "hitachi"),
            ("cinder-volume-ceph", "ceph"),
            ("some-backend", "unknown"),
        ]

        for app_name, expected in test_cases:
            with self.subTest(app_name=app_name):
                result = self.backend._get_backend_type(app_name)
                self.assertEqual(result, expected)

    def test_config_class(self):
        """Test configuration class retrieval."""
        config_class = self.backend.config_class
        self.assertEqual(config_class, StorageBackendConfig)

        # Test that we can create an instance with required fields
        config = config_class(name="test")
        self.assertEqual(config.name, "test")

    def test_commands(self):
        """Test command registration structure."""
        commands = self.backend.commands()

        self.assertIn("add", commands)
        self.assertIn("remove", commands)
        self.assertIn("config", commands)

        # Each command group should have a list of command dictionaries
        for group, command_list in commands.items():
            self.assertIsInstance(command_list, list)
            for cmd in command_list:
                self.assertIn("name", cmd)
                self.assertIn("command", cmd)

    def test_prompt_for_config(self):
        """Test configuration prompting (base implementation)."""
        # Base implementation returns empty dict
        result = self.backend._prompt_for_config()
        self.assertEqual(result, {})

    def test_create_add_plan(self):
        """Test add plan creation (base implementation)."""
        config = StorageBackendConfig(name="test")

        # Base implementation returns empty list
        result = self.backend._create_add_plan(self.deployment, config)
        self.assertEqual(result, [])

    def test_create_remove_plan(self):
        """Test remove plan creation (base implementation)."""
        # Base implementation returns empty list
        result = self.backend._create_remove_plan(self.deployment, "test-backend")
        self.assertEqual(result, [])


class TestStorageBackendExceptions(unittest.TestCase):
    """Test cases for storage backend exceptions."""

    def test_storage_backend_exception(self):
        """Test base storage backend exception."""
        exc = StorageBackendException("Test error")
        self.assertEqual(str(exc), "Test error")

    def test_backend_not_found_exception(self):
        """Test backend not found exception."""
        exc = BackendNotFoundException("Backend not found")
        self.assertIsInstance(exc, StorageBackendException)
        self.assertEqual(str(exc), "Backend not found")

    def test_backend_already_exists_exception(self):
        """Test backend already exists exception."""
        exc = BackendAlreadyExistsException("Backend exists")
        self.assertIsInstance(exc, StorageBackendException)
        self.assertEqual(str(exc), "Backend exists")

    def test_backend_validation_exception(self):
        """Test backend validation exception."""
        exc = BackendValidationException("Validation failed")
        self.assertIsInstance(exc, StorageBackendException)
        self.assertEqual(str(exc), "Validation failed")


if __name__ == "__main__":
    unittest.main()
