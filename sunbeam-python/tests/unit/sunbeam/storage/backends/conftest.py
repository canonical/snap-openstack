# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures and utilities for backend-specific tests."""

import pytest

from sunbeam.storage.backends.dellpowerstore.backend import DellPowerstoreBackend
from sunbeam.storage.backends.dellsc.backend import DellSCBackend
from sunbeam.storage.backends.fujitsueternusdx.backend import FujitsueternusdxBackend
from sunbeam.storage.backends.hitachi.backend import HitachiBackend
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
def fujitsueternusdx_backend():
    """Provide a Fujitsu ETERNUS DX backend instance."""
    return FujitsueternusdxBackend()


@pytest.fixture
def dellpowerstore_backend():
    """Provide a Dell PowerStore backend instance."""
    return DellPowerstoreBackend()


@pytest.fixture(params=["hitachi", "purestorage", "dellsc", "fujitsueternusdx", "dellpowerstore"])
def any_backend(request):
    """Parametrized fixture that provides each backend type."""
    backends = {
        "hitachi": HitachiBackend(),
        "purestorage": PureStorageBackend(),
        "dellsc": DellSCBackend(),
        "fujitsueternusdx": FujitsueternusdxBackend(),
        "dellpowerstore": DellPowerstoreBackend(),
    }
    return backends[request.param]
