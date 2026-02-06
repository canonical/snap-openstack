# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for Chaos Mesh functional tests.

These utilities centralise common operations so that multiple chaos scenarios
can reuse the same logic for:

- Enabling Sunbeam features.
- Inspecting Juju status via Jubilant.
- Waiting for units to become active again.
- Applying and deleting Chaos Mesh PodChaos resources.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import List, Tuple

import jubilant
import pytest

logger = logging.getLogger(__name__)


def get_leader_and_non_leaders(
    juju_client,
    app_name: str,
) -> Tuple[str, List[str]]:
    """Return (leader_unit_name, [non_leader_unit_names]) for a Juju app."""
    logger.info("Querying Juju status for application '%s' units...", app_name)

    status: jubilant.Status = juju_client.juju.status()
    app = status.apps[app_name]

    leader_unit: str | None = None
    non_leaders: List[str] = []
    for unit_name, unit_data in app.units.items():
        if getattr(unit_data, "leader", False):
            leader_unit = unit_name
        else:
            non_leaders.append(unit_name)

    if leader_unit is None:
        pytest.skip(
            f"No leader unit found for application '{app_name}' in Juju status."
        )

    return leader_unit, non_leaders


def wait_for_unit_active(
    juju_client,
    app_name: str,
    unit_name: str,
    timeout: int = 600,
) -> float:
    """Wait until the given Juju unit's workload status is 'active'.

    Returns the time (in seconds) taken for the unit to become active again.
    Raises AssertionError if the timeout is exceeded or the app enters error.
    """
    logger.info(
        "Waiting for unit %s (app '%s') to become active again...",
        unit_name,
        app_name,
    )
    start = time.time()

    try:
        juju_client.juju.wait(
            lambda status: is_unit_active(status, app_name, unit_name),
            error=lambda status: app_has_error(status, app_name),
            _timeout=timeout,
            _delay=5.0,
        )
    except jubilant.WaitError as exc:
        raise AssertionError(
            f"Application '{app_name}' entered error state while waiting for "
            f"{unit_name} to recover."
        ) from exc

    elapsed = time.time() - start
    logger.info(
        "Unit %s (app '%s') is active again after %.1f seconds.",
        unit_name,
        app_name,
        elapsed,
    )
    return elapsed


def is_unit_active(
    status: jubilant.Status,
    app_name: str,
    unit_name: str,
) -> bool:
    """Return True if the given unit's workload status is 'active'."""
    units = status.get_units(app_name)
    unit = units.get(unit_name)
    if not unit:
        return False
    workload = getattr(getattr(unit, "workload_status", None), "current", None)
    return workload == "active"


def app_has_error(status: jubilant.Status, app_name: str) -> bool:
    """Return True if any unit in the given app is in error."""
    return jubilant.any_error(status, app_name)


def unit_name_to_pod_name(unit_name: str) -> str:
    """Map a Juju unit name (e.g. 'keystone/1') to a pod name (e.g. 'keystone-1').

    For Kubernetes charms, Juju unit names and pod names follow this convention.
    """
    return unit_name.replace("/", "-")


def pod_chaos_name_for_pod(app_name: str, pod_name: str) -> str:
    """Return a deterministic PodChaos name for a given pod."""
    return f"{app_name}-{pod_name}-pod-kill"


def apply_pod_chaos_for_pod(
    app_namespace: str,
    pod_name: str,
    chaos_namespace: str = "chaos-mesh",
    *,
    duration: str = "30s",
    action: str = "pod-kill",
) -> str:
    """Create a PodChaos resource targeting a single pod.

    Returns the name of the created PodChaos resource.
    """
    chaos_name = pod_chaos_name_for_pod(app_namespace, pod_name)
    manifest = f"""
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata:
  name: {chaos_name}
  namespace: {chaos_namespace}
spec:
  action: {action}
  mode: one
  duration: "{duration}"
  selector:
    pods:
      {app_namespace}:
        - {pod_name}
""".lstrip()
    logger.info(
        "Applying PodChaos for pod %s in namespace %s (resource: %s)",
        pod_name,
        app_namespace,
        chaos_name,
    )
    subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest,
        check=True,
        capture_output=True,
        text=True,
    )
    return chaos_name


def delete_pod_chaos(chaos_name: str, chaos_namespace: str = "chaos-mesh") -> None:
    """Delete a PodChaos resource by name."""
    logger.info(
        "Deleting PodChaos resource: %s (namespace: %s)", chaos_name, chaos_namespace
    )
    subprocess.run(
        [
            "kubectl",
            "delete",
            "podchaos",
            chaos_name,
            "-n",
            chaos_namespace,
            "--ignore-not-found=true",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
