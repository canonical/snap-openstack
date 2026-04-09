# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Common fixtures and utilities for backend-specific tests."""

import pytest

from sunbeam.storage.backends.dellsc.backend import DellSCBackend
from sunbeam.storage.backends.hitachi.backend import HitachiBackend
from sunbeam.storage.backends.ibmstorwizesvc.backend import IbmstorwizesvcBackend
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
def ibmstorwizesvc_backend():
    """Provide an IBM Storwize SVC backend instance."""
    return IbmstorwizesvcBackend()


@pytest.fixture(params=["hitachi", "purestorage", "dellsc", "ibmstorwizesvc"])
def any_backend(request):
    """Parametrized fixture that provides each backend type."""
    backends = {
        "hitachi": HitachiBackend(),
        "purestorage": PureStorageBackend(),
        "dellsc": DellSCBackend(),
        "ibmstorwizesvc": IbmstorwizesvcBackend(),
    }
    return backends[request.param]
