# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest


def pytest_configure(config):
    """Patch is_feature_gate_enabled during import of MAAS modules.

    The module-level code in sunbeam.provider.maas.steps calls
    RoleTags.enabled_values() at import time, which triggers
    is_feature_gate_enabled and requires a snap environment.
    Patch only during import, then restore the original function.
    """
    import sunbeam.feature_gates

    patcher = patch.object(
        sunbeam.feature_gates, "is_feature_gate_enabled", return_value=False
    )
    try:
        patcher.start()
        import sunbeam.provider.maas.steps  # noqa: F401
    finally:
        patcher.stop()


@pytest.fixture(autouse=True)
def _mock_split_roles_disabled():
    """Default split_roles_enabled to False for all MAAS tests.

    Tests that need split-roles enabled should override this
    with their own @patch decorator.
    """
    with patch("sunbeam.provider.maas.steps.split_roles_enabled", return_value=False):
        yield
