# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Chaos tests for database access-path degradation (mysql-router pods)."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)


ROUTER_APPS: list[str] = [
    "nova-api-mysql-router",
    "nova-cell-mysql-router",
    "nova-mysql-router",
    "cinder-mysql-router",
    "cinder-volume-mysql-router",
    "neutron-mysql-router",
    "keystone-mysql-router",
    "glance-mysql-router",
    "placement-mysql-router",
    # "aodh-mysql-router",
    # "gnocchi-mysql-router",
    # "masakari-mysql-router",
    # "watcher-mysql-router",
    "horizon-mysql-router",
]


@pytest.mark.functional
def test_validation_resilient_to_mysql_router_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate mysql-router pod kills."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=[(app, []) for app in ROUTER_APPS],
        suite_name="mysql-router",
        report_name="test_validation_db_router_chaos",
        initial_delay=60,
        recovery_timeout=1800,
        poll_interval=10,
        quick_test_timeout=1800,
    )
