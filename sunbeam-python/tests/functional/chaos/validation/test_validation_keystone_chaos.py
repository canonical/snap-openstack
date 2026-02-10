# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0


"""Keystone-specific chaos tests for the validation feature."""

import logging

import pytest

from tests.functional.chaos.utils import (
    run_validation_with_pod_chaos,
)

logger = logging.getLogger(__name__)

KEYSTONE_APP = "keystone"
TRAEFIK_APPS = ["traefik-public", "traefik-internal"]


@pytest.mark.functional
def test_validation_resilient_to_non_leader_keystone_pod_kills(
    sunbeam_client,
    juju_client,
) -> None:
    """Validation 'smoke' profile should tolerate non-leader Keystone pod kills.

    This test:

    - Ensures the ``validation`` feature is enabled.
    - Uses Jubilant status to discover the Keystone leader unit and its
      non-leader units in the ``openstack`` model.
    - Starts ``sunbeam validation run smoke``
    - While validation is running, sequentially applies Chaos Mesh ``PodChaos``
      resources that kill each **non-leader** Keystone pod in turn, waiting for
      each unit to recover to ``workload-status: active``.
    - Collects and logs the recovery time for each non-leader unit.

    The expectation is that the validation smoke run completes successfully
    despite transient failures of non-leader Keystone pods.
    """
    run_validation_with_pod_chaos(
        juju_client,
        targets=[(KEYSTONE_APP, TRAEFIK_APPS)],
        suite_name="Keystone API",
    )
