# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Storage backend models and exceptions."""

from typing import Any, Dict

from pydantic import BaseModel, Field

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


class StorageBackendConfig(BaseModel):
    """Base configuration model for storage backends."""

    name: str = Field(..., description="Backend name")


class StorageBackendInfo(BaseModel):
    """Information about a deployed storage backend."""

    name: str
    backend_type: str
    status: str
    charm: str
    config: Dict[str, Any] = {}
