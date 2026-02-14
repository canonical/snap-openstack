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
from typing import List, Sequence, Tuple

import jubilant

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
        raise AssertionError(
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


def assert_apps_healthy(juju_client, app_names: List[str]) -> None:
    """Assert that the given applications have no units in error.

    If none of the applications are present in the model, this function logs
    a warning and returns without failing the test. This allows the same test
    suite to run against deployments that may not include all optional apps.
    """
    status: jubilant.Status = juju_client.juju.status()
    present_apps = [name for name in app_names if name in status.apps]

    if not present_apps:
        logger.warning(
            "None of the apps %s found in Juju model; "
            "skipping health assertion for them.",
            app_names,
        )
        return

    for app_name in present_apps:
        if jubilant.any_error(status, app_name):
            raise AssertionError(
                f"Application '{app_name}' has units in error state during chaos."
            )
        logger.info(
            "Application '%s' is healthy during chaos (no units in error).", app_name
        )


def unit_name_to_pod_name(unit_name: str) -> str:
    """Map a Juju unit name (e.g. 'keystone/1') to a pod name (e.g. 'keystone-1').

    For Kubernetes charms, Juju unit names and pod names follow this convention.
    """
    return unit_name.replace("/", "-")


def pod_chaos_name_for_pod(app_name: str, pod_name: str) -> str:
    """Return a deterministic PodChaos name for a given pod."""
    return f"{app_name}-{pod_name}-pod-kill"


def _kubectl_command(args: List[str]) -> List[str]:
    """Build a kubectl command suitable for the environment.

    ``juju exec --unit <unit> -m <model> -- sudo k8s kubectl ...``
    """
    k8s_unit = "k8s/0"
    k8s_model = "openstack-machines"
    return [
        "juju",
        "exec",
        "--unit",
        k8s_unit,
        "-m",
        k8s_model,
        "--",
        "sudo",
        "k8s",
        "kubectl",
        *args,
    ]


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
        _kubectl_command(["apply", "-f", "-"]),
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
        _kubectl_command(
            [
                "delete",
                "podchaos",
                chaos_name,
                "-n",
                chaos_namespace,
                "--ignore-not-found=true",
            ]
        ),
        check=False,
        capture_output=True,
        text=True,
    )


def run_validation_with_pod_chaos(
    juju_client,
    targets: Sequence[tuple[str, List[str]]],
    *,
    suite_name: str,
    openstack_namespace: str = "openstack",
    chaos_namespace: str = "chaos-mesh",
    validation_timeout: int = 3600,
) -> None:
    """Run 'sunbeam validation run smoke' while injecting PodChaos for targets.

    Each entry in ``targets`` is (application_name, dependent_applications).
    For each target application, all non-leader units are killed one by one
    using PodChaos, and we wait for them to return to active status while
    asserting that dependent applications remain healthy.
    """
    logger.info(
        "Starting 'sunbeam validation run smoke' for %s chaos suite...",
        suite_name,
    )
    validation_proc = subprocess.Popen(
        ["sunbeam", "validation", "run", "smoke"],
        text=True,
    )

    chaos_resources: List[str] = []
    try:
        for app_name, dependent_apps in targets:
            leader_unit, non_leader_units = get_leader_and_non_leaders(
                juju_client,
                app_name,
            )

            if not non_leader_units:
                logger.info(
                    "Application '%s' has no non-leader units; skipping chaos.",
                    app_name,
                )
                continue

            logger.info(
                "%s leader unit: %s; non-leaders: %s",
                app_name,
                leader_unit,
                non_leader_units,
            )

            for unit_name in non_leader_units:
                pod_name = unit_name_to_pod_name(unit_name)
                chaos_name = apply_pod_chaos_for_pod(
                    openstack_namespace,
                    pod_name,
                    chaos_namespace=chaos_namespace,
                    duration="30s",
                )
                chaos_resources.append(chaos_name)

                wait_for_unit_active(
                    juju_client,
                    app_name,
                    unit_name,
                    timeout=600,
                )

                if dependent_apps:
                    assert_apps_healthy(juju_client, dependent_apps)

        logger.info(
            "Waiting for validation smoke run to complete after %s chaos suite...",
            suite_name,
        )
        try:
            return_code = validation_proc.wait(timeout=validation_timeout)
        except subprocess.TimeoutExpired:
            validation_proc.kill()
            raise AssertionError(
                "sunbeam validation run smoke did not complete within the timeout."
            )

        assert return_code == 0, (
            "sunbeam validation run smoke failed with exit code "
            f"{return_code} during {suite_name} chaos suite."
        )
    finally:
        for chaos_name in chaos_resources:
            try:
                delete_pod_chaos(chaos_name, chaos_namespace=chaos_namespace)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to clean up PodChaos %s: %s", chaos_name, exc)

        if validation_proc.poll() is None:
            validation_proc.terminate()
