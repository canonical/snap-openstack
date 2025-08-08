# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Sunbeam Storage Backend Implementations.

This package contains implementations of various storage backends for Sunbeam.
"""

from sunbeam.storage.backends.hitachi import HitachiBackend

__all__ = [
    "HitachiBackend",
]
