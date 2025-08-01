# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sunbeam Storage Backends.

This module provides a pluggable storage backend system for Sunbeam.
"""

# Import backends to register them
import sunbeam.storage.backends.hitachi  # noqa: F401
from sunbeam.storage.base import StorageBackendBase
from sunbeam.storage.models import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    BackendValidationException,
    StorageBackendConfig,
    StorageBackendException,
    StorageBackendInfo,
)
from sunbeam.storage.service import StorageBackendService
from sunbeam.storage.registry import StorageBackendRegistry, storage_backend_registry

__all__ = [
    "StorageBackendBase",
    "StorageBackendConfig",
    "StorageBackendInfo",
    "StorageBackendService",
    "StorageBackendException",
    "BackendNotFoundException",
    "BackendAlreadyExistsException",
    "BackendValidationException",
    "StorageBackendRegistry",
    "storage_backend_registry",
]
