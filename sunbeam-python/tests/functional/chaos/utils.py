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

import base64
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
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
    try:
        app = status.apps[app_name]
    except KeyError as exc:
        available_apps = ", ".join(sorted(status.apps.keys()))
        raise RuntimeError(
            f"Application '{app_name}' not found in Juju status. "
            f"Available applications: {available_apps}"
        ) from exc

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
            timeout=timeout,
            delay=5.0,
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


def run_validation_command(
    cmd: List[str],
    timeout: int = 600,
) -> Tuple[float, str, bool]:
    """Run a validation command (e.g. sunbeam validation run quick).

    Returns (duration_seconds, output, success).
    """
    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    duration = time.time() - start
    output = (result.stdout or "") + (result.stderr or "")
    return (round(duration, 1), output, result.returncode == 0)


def wait_for_unit_active_with_tracking(
    juju_client,
    app_name: str,
    unit_name: str,
    timeout: int = 600,
    poll_interval: int = 10,
) -> Tuple[float | None, List[dict]]:
    """Poll until unit is active or timeout.

    Returns time_to_return_active_seconds (or None) and state_sequence.

    state_sequence: list of {timestamp_iso, state, message} when not active.
    """
    state_sequence: List[dict] = []
    poll_start = time.time()
    left_active_at: float | None = None

    while (time.time() - poll_start) < timeout:
        status = juju_client.juju.status()
        current, message = get_unit_workload_status(status, app_name, unit_name)
        now = time.time()
        ts_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

        if current != "active":
            if left_active_at is None:
                left_active_at = now
            state_sequence.append(
                {
                    "timestamp": ts_iso,
                    "state": current,
                    "message": message,
                }
            )
        else:
            if left_active_at is not None:
                return (round(now - left_active_at, 1), state_sequence)
            return (0.0, state_sequence)

        time.sleep(poll_interval)

    return (None, state_sequence)


def get_unit_workload_status(
    status: jubilant.Status,
    app_name: str,
    unit_name: str,
) -> Tuple[str, str]:
    """Return (workload_status.current, workload_status.message) for the unit.

    Returns ("unknown", "") if the unit or workload_status is missing.
    """
    units = status.get_units(app_name)
    unit = units.get(unit_name)
    if not unit:
        return ("unknown", "")
    workload = getattr(unit, "workload_status", None)
    current = getattr(workload, "current", None) or "unknown"
    message = getattr(workload, "message", None) or ""
    return (str(current), str(message))


def is_unit_active(
    status: jubilant.Status,
    app_name: str,
    unit_name: str,
) -> bool:
    """Return True if the given unit's workload status is 'active'."""
    current, _ = get_unit_workload_status(status, app_name, unit_name)
    return current == "active"


def app_has_error(status: jubilant.Status, app_name: str) -> bool:
    """Return True if any unit in the given app is in error."""
    return jubilant.any_error(status, app_name)


def get_status_json_for_apps(juju_client, app_names: List[str]) -> dict:
    """Return juju status as a dict restricted to the given application names.

    Runs ``juju status --format json`` and returns a structure with only
    the requested applications (for use in report snapshots).
    """
    try:
        raw = juju_client.juju.cli("status", "--format", "json")
        full = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not get juju status JSON: %s", exc)
        return {"_error": str(exc)}

    apps = full.get("applications") or {}
    return {"applications": {name: apps[name] for name in app_names if name in apps}}


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

    ``juju exec --unit <unit> -m <model> --stdin -- sudo k8s kubectl ...``
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


def _helm_command(args: List[str]) -> List[str]:
    """Build a helm command targeting the Sunbeam K8s cluster.

    ``juju exec --unit <unit> -m <model> -- sudo helm ...``
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
        "helm",
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

    manifest_b64 = base64.b64encode(manifest.encode("utf-8")).decode("ascii")
    cmd = [
        "juju",
        "exec",
        "--unit",
        "k8s/0",
        "-m",
        "openstack-machines",
        "--",
        "bash",
        "-c",
        'echo "$1" | base64 -d | sudo k8s kubectl apply -f -',
        "_",
        manifest_b64,
    ]
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(
            "Failed to apply PodChaos %s (exit code %s).\nstdout:\n%s\nstderr:\n%s",
            chaos_name,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        raise RuntimeError(
            f"kubectl apply for PodChaos '{chaos_name}' failed with exit code "
            f"{result.returncode}: {result.stderr.strip()}"
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


def _write_chaos_json_report(report_name: str, data: dict) -> Path:
    """Write a single JSON report file; name includes timestamp. Returns path."""
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    path = reports_dir / f"{report_name}_{timestamp}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return path


def run_validation_with_pod_chaos(  # noqa: C901
    juju_client,
    targets: Sequence[tuple[str, List[str]]],
    *,
    suite_name: str,
    report_name: str | None = None,
    openstack_namespace: str = "openstack",
    chaos_namespace: str = "chaos-mesh",
    validation_timeout: int = 3600,
    initial_delay: int = 60,
    recovery_timeout: int = 600,
    poll_interval: int = 10,
    quick_test_timeout: int = 600,
) -> None:
    """Run validation with PodChaos and optional JSON reporting.

    Smoke runs in parallel with chaos; quick run and JSON report
    are executed after chaos when report_name is provided.
    """
    logger.info(
        "Starting 'sunbeam validation run smoke' for %s chaos suite...",
        suite_name,
    )
    validation_proc = subprocess.Popen(
        ["sunbeam", "validation", "run", "smoke"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    run_start_monotonic = time.time()
    failed_recoveries: List[dict] = []
    apps_in_error: List[dict] = []
    recovery_per_unit: List[dict] = []
    validation_return_code: int | None = None
    error_summary: str | None = None
    validation_output: str | None = None
    smoke_duration: float | None = None

    chaos_resources: List[str] = []

    try:
        if initial_delay > 0:
            logger.info(
                "Sleeping %s seconds before starting PodChaos injections to allow "
                "Tempest discover-tempest-config/bootstrap to complete.",
                initial_delay,
            )
            time.sleep(initial_delay)

        for app_name, dependent_apps in targets:
            leader_unit, non_leader_units = get_leader_and_non_leaders(
                juju_client,
                app_name,
            )
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

                time_to_return_active_seconds, state_sequence = (
                    wait_for_unit_active_with_tracking(
                        juju_client,
                        app_name,
                        unit_name,
                        timeout=recovery_timeout,
                        poll_interval=poll_interval,
                    )
                )
                recovery_per_unit.append(
                    {
                        "app": app_name,
                        "unit": unit_name,
                        "time_to_return_active_seconds": time_to_return_active_seconds,
                    }
                )
                if state_sequence:
                    apps_in_error.append(
                        {
                            "app": app_name,
                            "unit": unit_name,
                            "state_sequence": state_sequence,
                        }
                    )
                if time_to_return_active_seconds is None:
                    failed_recoveries.append(
                        {
                            "app": app_name,
                            "unit": unit_name,
                            "pod": pod_name,
                            "error": "timeout",
                        }
                    )
                    break
                if dependent_apps:
                    assert_apps_healthy(juju_client, dependent_apps)

        logger.info(
            "Waiting for validation smoke run to complete after %s chaos suite...",
            suite_name,
        )
        try:
            stdout_data, _ = validation_proc.communicate(timeout=validation_timeout)
            validation_output = stdout_data or ""
            validation_return_code = validation_proc.returncode
            smoke_duration = time.time() - run_start_monotonic
        except subprocess.TimeoutExpired:
            validation_proc.kill()
            stdout_data, _ = validation_proc.communicate()
            validation_output = stdout_data or ""
            smoke_duration = time.time() - run_start_monotonic
            validation_return_code = None
            error_summary = (
                "sunbeam validation run smoke did not complete within the timeout."
            )

        if report_name:
            quick_duration, quick_output, quick_success = run_validation_command(
                ["sunbeam", "validation", "run", "quick"],
                timeout=quick_test_timeout,
            )
            test_duration = time.time() - run_start_monotonic
            final_status = "SUCCESS"
            if failed_recoveries or not quick_success:
                final_status = "FAIL"
            report_data = {
                "status": final_status,
                "test_duration_seconds": round(test_duration, 1),
                "smoke_test": {
                    "duration_seconds": round(smoke_duration or 0, 1),
                    "output": (validation_output or "")[:10000],
                    "success": validation_return_code == 0,
                },
                "apps_in_error": apps_in_error,
                "recovery_per_unit": recovery_per_unit,
                "quick_test": {
                    "duration_seconds": quick_duration,
                    "output": (quick_output or "")[:10000],
                    "success": quick_success,
                },
            }
            report_path = _write_chaos_json_report(
                f"{final_status}_{report_name}",
                report_data,
            )
            logger.info("Chaos report written to %s", report_path)

            if not quick_success:
                # Quick validation failure makes the chaos run a FAIL.
                raise AssertionError(
                    "Quick validation test failed after chaos. See reports/."
                )

        if failed_recoveries and error_summary is None:
            failed_labels = ", ".join(
                f"{fr['app']}/{fr['unit']}" for fr in failed_recoveries
            )
            error_summary = (
                f"One or more chaos targets did not recover cleanly: {failed_labels}"
            )
            raise AssertionError(error_summary)
    except Exception as exc:  # noqa: BLE001
        if error_summary is None:
            error_summary = repr(exc)
        raise
    finally:
        for chaos_name in chaos_resources:
            try:
                delete_pod_chaos(chaos_name, chaos_namespace=chaos_namespace)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to clean up PodChaos %s: %s", chaos_name, exc)

        if validation_proc.poll() is None:
            validation_proc.terminate()
