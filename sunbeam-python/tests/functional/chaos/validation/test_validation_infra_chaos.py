# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Chaos tests for core infrastructure services (MySQL, RabbitMQ, Traefik)."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)


INFRA_APPS: list[str] = [
    "mysql",
    "rabbitmq",
    "traefik-public",
    "traefik",
    "traefik-rgw",
]


@pytest.mark.functional
def test_validation_resilient_to_infra_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate infra pod/unit loss."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=[(app, []) for app in INFRA_APPS],
        suite_name="infra",
        report_name="test_validation_infra_chaos",
        initial_delay=60,
        recovery_timeout=1800,
        poll_interval=10,
        quick_test_timeout=1800,
    )
