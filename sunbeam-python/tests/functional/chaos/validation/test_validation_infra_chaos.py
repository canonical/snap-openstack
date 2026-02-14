# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Chaos tests for core infrastructure services (MySQL, RabbitMQ, Traefik)."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)


INFRA_TARGETS: list[tuple[str, list[str]]] = [
    ("mysql", ["keystone", "traefik-public", "traefik-internal"]),
    ("rabbitmq", ["keystone", "traefik-public", "traefik-internal"]),
    ("traefik-public", ["keystone"]),
    ("traefik", ["keystone"]),
    ("traefik-rgw", ["keystone"]),
]


@pytest.mark.functional
def test_validation_resilient_to_infra_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate infra pod/unit loss."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=INFRA_TARGETS,
        suite_name="infra",
    )
