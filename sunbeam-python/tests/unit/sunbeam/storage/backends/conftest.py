# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures and utilities for backend-specific tests."""

import pytest

from sunbeam.storage.backends.dellpowerstore.backend import DellPowerstoreBackend
from sunbeam.storage.backends.dellsc.backend import DellSCBackend
from sunbeam.storage.backends.hitachi.backend import HitachiBackend
from sunbeam.storage.backends.nexenta.backend import NexentaBackend
from sunbeam.storage.backends.purestorage.backend import PureStorageBackend


@pytest.fixture
def hitachi_backend():
    """Provide a Hitachi backend instance."""
    return HitachiBackend()


@pytest.fixture
def purestorage_backend():
    """Provide a Pure Storage backend instance."""
    return PureStorageBackend()


@pytest.fixture
def dellsc_backend():
    """Provide a Dell Storage Center backend instance."""
    return DellSCBackend()


@pytest.fixture
def nexenta_backend():
    """Provide a Nexenta backend instance."""
    return NexentaBackend()


@pytest.fixture
def dellpowerstore_backend():
    """Provide a Dell PowerStore backend instance."""
    return DellPowerstoreBackend()


@pytest.fixture(
    params=["hitachi", "purestorage", "dellsc", "nexenta", "dellpowerstore"]
)
def any_backend(request):
    """Parametrized fixture that provides each backend type."""
    backends = {
        "hitachi": HitachiBackend(),
        "purestorage": PureStorageBackend(),
        "dellsc": DellSCBackend(),
        "nexenta": NexentaBackend(),
        "dellpowerstore": DellPowerstoreBackend(),
    }
    return backends[request.param]
