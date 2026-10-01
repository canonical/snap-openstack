# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock, Mock, patch

import pytest
from lightkube.core.exceptions import ApiError

from sunbeam.core.common import ResultType
from sunbeam.features.caas.feature import (
    CAAPH_CONTAINER,
    CAAPH_DEPLOYMENT,
    CAAPH_NAMESPACE,
    PatchCaaphProxyStep,
    SetupClusterAPI,
    _clusterctl_error,
)


@pytest.fixture
def client():
    return Mock()


@pytest.fixture
def kube_client():
    return MagicMock()


@pytest.fixture
def get_kube_client_patch(kube_client):
    with patch(
        "sunbeam.features.caas.feature.get_kube_client", return_value=kube_client
    ) as mock:
        yield mock


class TestPatchCaaphProxyStep:
    def test_is_skip_no_proxy_settings(self, client, step_context):
        """Skip when proxy settings are empty."""
        step = PatchCaaphProxyStep(client, {})
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_is_skip_with_proxy_settings(
        self,
        client,
        get_kube_client_patch,
        kube_client,
        step_context,
    ):
        """Proceed when proxy settings are present."""
        proxy_settings = {
            "HTTP_PROXY": "http://squid.internal:3128",
            "HTTPS_PROXY": "http://squid.internal:3128",
            "NO_PROXY": "localhost,.example.com",
        }
        step = PatchCaaphProxyStep(client, proxy_settings)
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        get_kube_client_patch.assert_called_once_with(client)

    def test_is_skip_kube_client_error(self, client, step_context):
        """Fail when kube client cannot be created."""
        from sunbeam.steps.k8s import KubeClientError

        with patch(
            "sunbeam.features.caas.feature.get_kube_client",
            side_effect=KubeClientError("connection error"),
        ):
            proxy_settings = {"HTTP_PROXY": "http://squid.internal:3128"}
            step = PatchCaaphProxyStep(client, proxy_settings)
            result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED

    def test_run_patches_deployment(
        self, client, get_kube_client_patch, kube_client, step_context
    ):
        """Patch is applied to caaph-controller-manager with proxy env vars."""
        proxy_settings = {
            "HTTP_PROXY": "http://squid.internal:3128",
            "HTTPS_PROXY": "http://squid.internal:3128",
            "NO_PROXY": "localhost,.example.com",
        }
        step = PatchCaaphProxyStep(client, proxy_settings)
        step.is_skip(step_context)  # sets step.kube
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        kube_client.patch.assert_called_once()
        call_kwargs = kube_client.patch.call_args
        assert call_kwargs.kwargs["namespace"] == CAAPH_NAMESPACE
        patch_body = call_kwargs.args[2]
        containers = patch_body["spec"]["template"]["spec"]["containers"]
        assert containers[0]["name"] == CAAPH_CONTAINER
        env_names = {e["name"] for e in containers[0]["env"]}
        assert env_names == {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}

    def test_run_skips_empty_proxy_values(
        self,
        client,
        get_kube_client_patch,
        kube_client,
        step_context,
    ):
        """Proxy env vars with empty values are excluded from the patch."""
        proxy_settings = {
            "HTTP_PROXY": "http://squid.internal:3128",
            "HTTPS_PROXY": "",
            "NO_PROXY": "localhost",
        }
        step = PatchCaaphProxyStep(client, proxy_settings)
        step.is_skip(step_context)
        result = step.run(step_context)

        assert result.result_type == ResultType.COMPLETED
        patch_body = kube_client.patch.call_args.args[2]
        containers = patch_body["spec"]["template"]["spec"]["containers"]
        env_names = {e["name"] for e in containers[0]["env"]}
        assert "HTTPS_PROXY" not in env_names
        assert "HTTP_PROXY" in env_names
        assert "NO_PROXY" in env_names

    def test_run_patches_correct_deployment(
        self,
        client,
        get_kube_client_patch,
        kube_client,
        step_context,
    ):
        """The patch targets the correct deployment name."""
        proxy_settings = {"HTTP_PROXY": "http://squid.internal:3128"}
        step = PatchCaaphProxyStep(client, proxy_settings)
        step.is_skip(step_context)
        step.run(step_context)

        call_args = kube_client.patch.call_args
        assert call_args.args[1] == CAAPH_DEPLOYMENT

    def test_run_api_error(
        self, client, get_kube_client_patch, kube_client, step_context
    ):
        """Return FAILED when kube API raises ApiError."""
        status = MagicMock()
        status.message = "not found"
        kube_client.patch.side_effect = ApiError(response=MagicMock(status_code=404))

        proxy_settings = {"HTTP_PROXY": "http://squid.internal:3128"}
        step = PatchCaaphProxyStep(client, proxy_settings)
        step.is_skip(step_context)
        result = step.run(step_context)

        assert result.result_type == ResultType.FAILED


def _provider(name, version, namespace=None):
    return {
        "metadata": {"name": name, "namespace": namespace or f"{name}-system"},
        "version": version,
    }


def _pod(name, phase="Running", ready=True, waiting_reason=None, waiting_message=None):
    pod = MagicMock()
    pod.metadata.name = name
    pod.status.phase = phase
    cs = MagicMock()
    cs.ready = ready
    if waiting_reason is not None:
        cs.state.waiting = MagicMock(reason=waiting_reason, message=waiting_message)
        cs.state.terminated = None
    else:
        cs.state = None
    pod.status.containerStatuses = [cs]
    return pod


class TestSetupClusterAPISkip:
    @pytest.fixture
    def step(self, client):
        return SetupClusterAPI(client, MagicMock(), MagicMock(), "k8s", {})

    @pytest.fixture
    def kube_list(self, step, get_kube_client_patch, kube_client):
        """Patch kube.list to serve providers and pods based on labels."""

        def _list(res, *, namespace="*", labels=None, **kwargs):
            if labels and "clusterctl.cluster.x-k8s.io/core" in labels:
                return _list.providers
            return _list.pods.get(labels["cluster.x-k8s.io/provider"], [])

        _list.providers = []
        _list.pods = {}
        kube_client.list.side_effect = _list
        return _list

    def test_no_providers(self, client, step, kube_list, step_context):
        """Fresh install: no providers deployed, step must run."""
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED

    def test_healthy_providers_skipped(self, client, step, kube_list, step_context):
        """Matching versions and running pods: skip the installation."""
        kube_list.providers = [_provider("cluster-api", "v1.12.3")]
        kube_list.pods = {"cluster-api": [_pod("capi-controller-manager")]}
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED

    def test_pod_not_running_fails(self, client, step, kube_list, step_context):
        """Broken provider pod from a previous failed run must fail the step."""
        kube_list.providers = [_provider("cluster-api", "v1.12.3")]
        kube_list.pods = {
            "cluster-api": [
                _pod(
                    "capi-controller-manager",
                    phase="Pending",
                    ready=False,
                    waiting_reason="ImagePullBackOff",
                    waiting_message="Back-off pulling image",
                )
            ]
        }
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "not healthy" in result.message

    def test_no_pods_fails(self, client, step, kube_list, step_context):
        """Provider with no pods at all is not healthy."""
        kube_list.providers = [_provider("cluster-api", "v1.12.3")]
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "not healthy" in result.message

    def test_version_mismatch_fails(self, client, step, kube_list, step_context):
        """Minor version difference is not supported."""
        kube_list.providers = [_provider("cluster-api", "v1.13.3")]
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.FAILED
        assert "Only micro version upgrade" in result.message

    def test_micro_version_change_runs(self, client, step, kube_list, step_context):
        """Micro version difference triggers the upgrade path."""
        kube_list.providers = [_provider("cluster-api", "v1.12.4")]
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.COMPLETED
        assert step.micro_version_changed

    def test_succeeded_pods_are_healthy(self, client, step, kube_list, step_context):
        """Succeeded pods (e.g. completed jobs) do not block the step."""
        kube_list.providers = [_provider("cluster-api", "v1.12.3")]
        kube_list.pods = {
            "cluster-api": [
                _pod("capi-controller-manager"),
                _pod("capi-job", phase="Succeeded", ready=False),
            ]
        }
        result = step.is_skip(step_context)
        assert result.result_type == ResultType.SKIPPED


class TestClusterctlError:
    def test_empty(self):
        assert _clusterctl_error(None) == ""
        assert _clusterctl_error("") == ""

    def test_filters_error_lines(self):
        stderr = "Installing providers\nError: failed to fetch provider\nDone\n"
        assert _clusterctl_error(stderr) == "Error: failed to fetch provider"

    def test_no_error_lines_returns_stderr(self):
        stderr = "some progress output"
        assert _clusterctl_error(stderr) == stderr

    def test_bytes_input(self):
        stderr = b"Error: boom\n"
        assert _clusterctl_error(stderr) == "Error: boom"
