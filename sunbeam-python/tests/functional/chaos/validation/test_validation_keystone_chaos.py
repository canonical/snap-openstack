# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0


"""Chaos Mesh tests for the validation feature.

These tests exercise the validation feature (``sunbeam validation run``) while
Chaos Mesh injects failures into **non-leader** Keystone pods, to assess how
well validation behaves under control plane disruption.
"""

import logging
import subprocess
from typing import List

import pytest

from tests.functional.chaos.utils import (
    apply_pod_chaos_for_pod,
    delete_pod_chaos,
    get_leader_and_non_leaders,
    unit_name_to_pod_name,
    wait_for_unit_active,
)

logger = logging.getLogger(__name__)

OPENSTACK_NAMESPACE = "openstack"
CHAOS_NAMESPACE = "chaos-mesh"
KEYSTONE_APP = "keystone"


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
    sunbeam_client.enable_feature("validation")
    leader_unit, non_leader_units = get_leader_and_non_leaders(
        juju_client,
        KEYSTONE_APP,
    )
    logger.info(
        "Keystone leader unit: %s; non-leaders: %s",
        leader_unit,
        non_leader_units,
    )

    # Start validation smoke tests in the background.
    logger.info("Starting 'sunbeam validation run smoke'...")
    validation_proc = subprocess.Popen(
        ["sunbeam", "validation", "run", "smoke"],
        text=True,
    )

    chaos_resources: List[str] = []
    try:
        for unit_name in non_leader_units:
            pod_name = unit_name_to_pod_name(unit_name)
            chaos_name = apply_pod_chaos_for_pod(
                OPENSTACK_NAMESPACE,
                pod_name,
                chaos_namespace=CHAOS_NAMESPACE,
                duration="30s",
            )
            chaos_resources.append(chaos_name)

            # Wait for the affected unit to become active again.
            wait_for_unit_active(
                juju_client,
                KEYSTONE_APP,
                unit_name,
                timeout=600,
            )

        # After injecting chaos to all non-leaders, wait for validation to finish.
        logger.info("Waiting for validation smoke run to complete...")
        try:
            return_code = validation_proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            validation_proc.kill()
            raise AssertionError(
                "sunbeam validation run smoke did not complete within the timeout."
            )

        assert return_code == 0, (
            f"sunbeam validation run smoke failed with exit code {return_code}"
        )
    finally:
        for chaos_name in chaos_resources:
            try:
                delete_pod_chaos(chaos_name, chaos_namespace=CHAOS_NAMESPACE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to clean up PodChaos %s: %s", chaos_name, exc)

        if validation_proc.poll() is None:
            validation_proc.terminate()
