# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for storage backend models."""

import pytest
from pydantic import ValidationError

from sunbeam.storage.models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    BackendValidationException,
    StorageBackendConfig,
    StorageBackendException,
    StorageBackendInfo,
)


class TestStorageBackendConfig:
    """Test cases for StorageBackendConfig model."""

    def test_valid_config(self):
        """Test creating valid configuration."""
        config = StorageBackendConfig(name="test-backend")
        assert config.name == "test-backend"

    def test_invalid_config_missing_name(self):
        """Test validation error for missing name."""
        with pytest.raises(ValidationError):
            StorageBackendConfig()

    def test_config_serialization(self):
        """Test configuration serialization."""
        config = StorageBackendConfig(name="test-backend")
        data = config.model_dump()
        assert data == {"name": "test-backend"}

    def test_config_from_dict(self):
        """Test creating configuration from dictionary."""
        data = {"name": "test-backend"}
        config = StorageBackendConfig(**data)
        assert config.name == "test-backend"


class TestStorageBackendInfo:
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
        assert info.name == "test-backend"
        assert info.backend_type == "hitachi"
        assert info.status == "active"
        assert info.charm == "cinder-volume-hitachi"
        assert info.config == {"key": "value"}

    def test_info_with_defaults(self):
        """Test creating info with default values."""
        info = StorageBackendInfo(
            name="test-backend",
            backend_type="hitachi",
            status="active",
            charm="cinder-volume-hitachi",
        )
        assert info.config == {}

    def test_info_serialization(self):
        """Test backend info serialization."""
        info = StorageBackendInfo(
            name="test-backend",
            backend_type="hitachi",
            status="active",
            charm="cinder-volume-hitachi",
            config={"key": "value"},
        )
        data = info.model_dump()
        expected = {
            "name": "test-backend",
            "backend_type": "hitachi",
            "status": "active",
            "charm": "cinder-volume-hitachi",
            "config": {"key": "value"},
        }
        assert data == expected

    def test_info_from_dict(self):
        """Test creating backend info from dictionary."""
        data = {
            "name": "test-backend",
            "backend_type": "hitachi",
            "status": "active",
            "charm": "cinder-volume-hitachi",
            "config": {"key": "value"},
        }
        info = StorageBackendInfo(**data)
        assert info.name == "test-backend"
        assert info.backend_type == "hitachi"
        assert info.status == "active"
        assert info.charm == "cinder-volume-hitachi"
        assert info.config == {"key": "value"}


class TestStorageBackendExceptions:
    """Test cases for storage backend exceptions."""

    def test_storage_backend_exception(self):
        """Test base storage backend exception."""
        exc = StorageBackendException("Test error")
        assert str(exc) == "Test error"
        assert isinstance(exc, Exception)

    def test_backend_not_found_exception(self):
        """Test backend not found exception."""
        exc = BackendNotFoundException("Backend not found")
        assert str(exc) == "Backend not found"
        assert isinstance(exc, StorageBackendException)

    def test_backend_already_exists_exception(self):
        """Test backend already exists exception."""
        exc = BackendAlreadyExistsException("Backend already exists")
        assert str(exc) == "Backend already exists"
        assert isinstance(exc, StorageBackendException)

    def test_backend_validation_exception(self):
        """Test backend validation exception."""
        exc = BackendValidationException("Validation failed")
        assert str(exc) == "Validation failed"
        assert isinstance(exc, StorageBackendException)

    def test_exception_inheritance(self):
        """Test exception inheritance hierarchy."""
        # All custom exceptions should inherit from StorageBackendException
        exceptions = [
            BackendNotFoundException("test"),
            BackendAlreadyExistsException("test"),
            BackendValidationException("test"),
        ]

        for exc in exceptions:
            assert isinstance(exc, StorageBackendException)
            assert isinstance(exc, Exception)

    def test_exception_with_no_message(self):
        """Test exceptions with no message."""
        exc = StorageBackendException()
        assert isinstance(exc, Exception)

        exc = BackendNotFoundException()
        assert isinstance(exc, StorageBackendException)

    def test_exception_chaining(self):
        """Test exception chaining."""
        original = ValueError("Original error")
        chained = None
        try:
            raise original
        except ValueError as e:
            try:
                raise StorageBackendException("Chained error") from e
            except StorageBackendException as chained_exc:
                chained = chained_exc

        assert str(chained) == "Chained error"
        assert chained.__cause__ is original
