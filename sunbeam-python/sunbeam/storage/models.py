# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend models and exceptions."""

from typing import Any, Dict

import pydantic

from sunbeam.core.common import SunbeamException

# =============================================================================
# Exceptions
# =============================================================================


class StorageBackendException(SunbeamException):
    """Base exception for storage backend operations."""

    pass


class BackendNotFoundException(StorageBackendException):
    """Raised when storage backend is not found."""

    pass


class BackendAlreadyExistsException(StorageBackendException):
    """Raised when storage backend already exists."""

    pass


class BackendValidationException(StorageBackendException):
    """Raised when storage backend configuration is invalid."""

    pass


# =============================================================================
# Data Models
# =============================================================================


class StorageBackendInfo(pydantic.BaseModel):
    """Information about a deployed storage backend."""

    name: str
    backend_type: str
    status: str
    charm: str
    config: Dict[str, Any] = {}


class SecretDictField:
    """Marker class to indicate a field needs to be managed as a juju secret.

    This class is used as a field annotation in Pydantic models to indicate that
    the field contains sensitive information (e.g., passwords, API tokens).

    The field name is the name of the key in the Juju secret dictionary.
    """

    def __init__(self, field: str):
        self.field = field

    def __repr__(self) -> str:
        """Return a string representation of the SecretDictField."""
        return f"SecretDictField(field={self.field})"
