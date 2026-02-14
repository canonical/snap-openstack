# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Generic API control-plane pod loss chaos tests."""

from __future__ import annotations

import logging

import pytest

from tests.functional.chaos.utils import run_validation_with_pod_chaos

logger = logging.getLogger(__name__)


API_TARGETS: list[tuple[str, list[str]]] = [
    ("nova", ["keystone", "traefik-public", "traefik-internal"]),
    ("neutron", ["keystone", "traefik-public", "traefik-internal"]),
    ("glance", ["keystone", "traefik-public", "traefik-internal"]),
    ("cinder-k8s", ["keystone", "traefik-public", "traefik-internal"]),
    ("placement", ["keystone", "traefik-public", "traefik-internal"]),
    ("aodh", ["keystone", "traefik-public", "traefik-internal"]),
    ("ceilometer", ["keystone", "traefik-public", "traefik-internal"]),
    ("gnocchi", ["keystone", "traefik-public", "traefik-internal"]),
    ("masakari", ["keystone", "traefik-public", "traefik-internal"]),
    ("watcher", ["keystone", "traefik-public", "traefik-internal"]),
    ("horizon", ["keystone", "traefik-public", "traefik-internal"]),
]


@pytest.mark.functional
def test_validation_resilient_to_non_leader_api_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate non-leader API pod kills."""
    run_validation_with_pod_chaos(
        juju_client,
        targets=API_TARGETS,
        suite_name="API pod",
    )
