# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sunbeam Storage Backends.

This module provides a pluggable storage backend system for Sunbeam.
"""

# Import backends to register them
import sunbeam.storage.backends.hitachi  # noqa: F401
from sunbeam.storage.basestorage import (
    BackendAlreadyExistsException,
    BackendNotFoundException,
    BackendValidationException,
    StorageBackendBase,
    StorageBackendConfig,
    StorageBackendException,
    StorageBackendInfo,
    StorageBackendService,
)
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
