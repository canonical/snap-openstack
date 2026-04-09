# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures and utilities for backend-specific tests."""

import pytest

from sunbeam.storage.backends.dellpowerstore.backend import DellPowerstoreBackend
from sunbeam.storage.backends.dellsc.backend import DellSCBackend
from sunbeam.storage.backends.hitachi.backend import HitachiBackend
from sunbeam.storage.backends.inspuras13000.backend import Inspuras13000Backend
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
def inspuras13000_backend():
    """Provide an Inspur AS13000 backend instance."""
    return Inspuras13000Backend()


@pytest.fixture
def dellpowerstore_backend():
    """Provide a Dell PowerStore backend instance."""
    return DellPowerstoreBackend()


@pytest.fixture(params=["hitachi", "purestorage", "dellsc", "inspuras13000", "dellpowerstore"])
def any_backend(request):
    """Parametrized fixture that provides each backend type."""
    backends = {
        "hitachi": HitachiBackend(),
        "purestorage": PureStorageBackend(),
        "dellsc": DellSCBackend(),
        "inspuras13000": Inspuras13000Backend(),
        "dellpowerstore": DellPowerstoreBackend(),
    }
    return backends[request.param]
