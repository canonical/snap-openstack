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
