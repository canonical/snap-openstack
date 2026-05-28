# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Generic API control-plane pod loss chaos tests."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)


API_APPS: list[str] = [
    "nova",
    "neutron",
    "glance",
    "cinder",
    "placement",
    # "aodh",
    # "ceilometer",
    # "gnocchi",
    # "masakari",
    # "watcher",
    "horizon",
]


@pytest.mark.functional
def test_validation_resilient_to_non_leader_api_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate non-leader API pod kills."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=[(app, []) for app in API_APPS],
        suite_name="API pod",
        report_name="test_validation_api_pod_chaos",
        initial_delay=60,
        recovery_timeout=1800,
        poll_interval=10,
        quick_test_timeout=1800,
    )
