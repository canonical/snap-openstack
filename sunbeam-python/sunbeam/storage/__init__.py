# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sunbeam Storage Backends.

This module provides a pluggable storage backend system for Sunbeam.
"""

# Import backends to register them
import sunbeam.storage.backends.hitachi.backend  # noqa: F401
