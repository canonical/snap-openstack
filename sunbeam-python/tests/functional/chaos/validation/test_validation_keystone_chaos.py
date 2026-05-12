# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0


"""Keystone-specific chaos tests for the validation feature."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)

KEYSTONE_APP = "keystone"
TRAEFIK_APPS = ["traefik-public", "traefik-internal"]


@pytest.mark.functional
def test_validation_resilient_to_non_leader_keystone_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Run smoke + quick validation around non-leader Keystone pod chaos."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=[(KEYSTONE_APP, TRAEFIK_APPS)],
        suite_name="Keystone API",
        report_name="test_validation_keystone_chaos",
        initial_delay=60,
        recovery_timeout=1800,
        poll_interval=10,
        quick_test_timeout=1800,
    )
