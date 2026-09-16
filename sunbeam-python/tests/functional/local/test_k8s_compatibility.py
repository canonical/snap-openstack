# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Run on separate, bootstrapped 1.32 and 1.36 local deployments."""

import json
import shlex
import socket
import subprocess
from pathlib import Path

import pytest

from . import utils


def kubectl(*args: str, resource: dict | None = None) -> dict:
    """Use the tested snap's client and server, including installed CRD schemas."""
    result = subprocess.run(
        ["sudo", "k8s", "kubectl", *args, "-o", "json"],
        input=json.dumps(resource) if resource is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def k8s_charm_channel() -> str:
    result = subprocess.run(
        ["juju", "status", "--model", "admin/openstack-machines", "--format", "json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    return status["applications"]["k8s"]["charm-channel"]


@pytest.fixture(scope="module")
def k8s_track(request: pytest.FixtureRequest) -> str:
    track = request.config.getoption("expected_k8s_track")
    if track is None:
        pytest.skip("Requires --expected-k8s-track on an isolated test deployment")
    request.getfixturevalue("ensure_local_cluster_bootstrapped")
    server_version = kubectl("version")["serverVersion"]["gitVersion"]
    assert server_version.startswith(f"v{track}.")
    assert k8s_charm_channel().split("/", 1)[0] == track
    return track


@pytest.mark.parametrize(
    "kind,namespace,version",
    [
        ("ciliumnodeconfigs.cilium.io", "kube-system", "cilium.io/v2"),
        ("ipaddresspools.metallb.io", "metallb-system", "metallb.io/v1beta1"),
        ("l2advertisements.metallb.io", "metallb-system", "metallb.io/v1beta1"),
    ],
)
def test_k8s_compatibility_resource_schemas(k8s_track, kind, namespace, version):
    resources = kubectl("get", kind, "--namespace", namespace)["items"]
    assert resources, f"No {kind} found on Kubernetes {k8s_track}"
    for resource in resources:
        assert resource["apiVersion"] == version
        metadata = resource["metadata"]
        # Check reapplication against the real admission webhooks and schemas,
        # without persisting field ownership changes from the test.
        candidate = {
            "apiVersion": resource["apiVersion"],
            "kind": resource["kind"],
            "metadata": {"name": metadata["name"], "namespace": namespace},
            "spec": resource["spec"],
        }
        kubectl(
            "apply",
            "--server-side",
            "--dry-run=server",
            "--force-conflicts",
            "--field-manager=sunbeam-compatibility-test",
            "-f",
            "-",
            resource=candidate,
        )


def test_k8s_compatibility_cilium_datapath(k8s_track):
    config = kubectl("get", "configmap", "cilium-config", "-n", "kube-system")["data"]
    replacement = config.get("kube-proxy-replacement") in ("true", "strict")
    if k8s_track == "1.36":
        assert replacement, "The 1.36 test must exercise kube-proxy replacement"
    configs = kubectl("get", "ciliumnodeconfigs.cilium.io", "-n", "kube-system")[
        "items"
    ]
    managed = [
        c for c in configs if c["metadata"]["name"].startswith("cilium-devices-")
    ]
    assert managed
    for resource in managed:
        defaults = resource["spec"]["defaults"]
        assert defaults["devices"]
        if replacement:
            assert defaults["direct-routing-device"] in defaults["devices"].split(",")
    pods = kubectl("get", "pods", "-n", "kube-system", "-l", "k8s-app=cilium")["items"]
    assert pods
    assert all(
        any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in pod["status"]["conditions"]
        )
        for pod in pods
    )
    kubectl("get", "storageclass", "csi-rawfile-default")


def test_k8s_compatibility_service_vips(k8s_track):
    """Verify the service datapath, including externalTrafficPolicy patches."""
    services = kubectl("get", "services", "--all-namespaces")["items"]
    loadbalancers = [s for s in services if s["spec"]["type"] == "LoadBalancer"]
    assert loadbalancers
    for service in loadbalancers:
        ingresses = service["status"]["loadBalancer"].get("ingress", [])
        assert ingresses, service["metadata"]["name"]
        for ingress in ingresses:
            for port in service["spec"]["ports"]:
                if port.get("protocol", "TCP") == "TCP":
                    with socket.create_connection(
                        (ingress["ip"], port["port"]), timeout=10
                    ):
                        pass


def test_k8s_compatibility_rejects_other_track(k8s_track, tmp_path: Path):
    channel = k8s_charm_channel()
    before = utils.get_latest_sunbeam_manifest()
    manifest_path = tmp_path / "other-track.yaml"
    other = "1.36" if k8s_track == "1.32" else "1.32"
    utils.apply_manifest(
        str(manifest_path),
        {"core": {"software": {"charms": {"k8s": {"channel": f"{other}/stable"}}}}},
    )
    command = shlex.join(
        [utils.SUNBEAM_BINARY, "cluster", "refresh", "--manifest", str(manifest_path)]
    )
    result = subprocess.run(
        ["sg", utils.SUNBEAM_GROUP, command], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "changes deployment track" in result.stdout + result.stderr
    assert utils.get_latest_sunbeam_manifest() == before
    assert k8s_charm_channel() == channel
