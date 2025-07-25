# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""
Sunbeam Storage Backends

This module provides a pluggable storage backend system for Sunbeam.
"""

from sunbeam.storage.basestorage import (
    StorageBackendBase,
    StorageBackendConfig,
    StorageBackendInfo,
    StorageBackendService,
    StorageBackendException,
    BackendNotFoundException,
    BackendAlreadyExistsException,
    BackendValidationException,
)

from sunbeam.storage.registry import StorageBackendRegistry, storage_backend_registry

# Import backends to register them
import sunbeam.storage.backends.hitachi  # noqa: F401

__all__ = [
    'StorageBackendBase',
    'StorageBackendConfig',
    'StorageBackendInfo',
    'StorageBackendService',
    'StorageBackendException',
    'BackendNotFoundException',
    'BackendAlreadyExistsException',
    'BackendValidationException',
    'StorageBackendRegistry',
    'storage_backend_registry',
]