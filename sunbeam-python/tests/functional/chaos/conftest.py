# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: I001

"""Functional fixtures and config hooks for chaos tests."""

import logging
import subprocess

import pytest

from tests.functional.chaos.utils import _helm_command, _kubectl_command
from tests.functional.feature.conftest import (  # noqa: F401
    juju_client,
    sunbeam_client as _feature_sunbeam_client,
    test_config,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def ensure_chaos_mesh_installed() -> None:
    """Ensure Chaos Mesh is installed and ready for chaos tests.

    This follows the documented Helm installation path, using:

    - sudo snap install helm --classic
    - helm repo add chaos-mesh https://charts.chaos-mesh.org
    - helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh
    """

    def _has_chaos_mesh() -> bool:
        try:
            subprocess.run(
                _kubectl_command(["get", "pods", "-n", "chaos-mesh"]),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return False

        return True

    if _has_chaos_mesh():
        logger.info("Chaos Mesh already present in namespace 'chaos-mesh'.")
        return

    logger.info("Chaos Mesh not detected; attempting to install via Helm...")

    try:
        subprocess.run(
            _helm_command(["version"]),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        logger.info(
            "Helm not found or not working in k8s/0@openstack-machines; "
            "attempting to install helm snap in that unit...",
        )
        subprocess.run(
            [
                "juju",
                "exec",
                "--unit",
                "k8s/0",
                "-m",
                "openstack-machines",
                "--",
                "sudo",
                "snap",
                "install",
                "helm",
                "--classic",
            ],
            check=True,
            text=True,
        )
        # Re-check helm availability; if this fails, surface a clear error.
        try:
            subprocess.run(
                _helm_command(["version"]),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:  # pragma: no cover
            msg = (
                "Helm is still not available in k8s/0@openstack-machines after "
                "attempted snap installation. Please log into that unit and "
                "ensure 'helm' is installed and configured."
            )
            raise RuntimeError(msg) from exc

    subprocess.run(
        _helm_command(["repo", "add", "chaos-mesh", "https://charts.chaos-mesh.org"]),
        check=False,
        text=True,
    )
    subprocess.run(
        _helm_command(["repo", "update"]),
        check=False,
        text=True,
    )

    subprocess.run(
        _helm_command(
            [
                "upgrade",
                "--install",
                "chaos-mesh",
                "chaos-mesh/chaos-mesh",
                "--namespace",
                "chaos-mesh",
                "--create-namespace",
            ],
        ),
        check=True,
        text=True,
    )

    if not _has_chaos_mesh():
        logger.warning(
            "Chaos Mesh could not be verified as running in 'chaos-mesh' "
            "namespace after attempted installation. "
            "Continuing anyway; PodChaos operations may fail if Chaos Mesh "
            "is not fully ready.",
        )


@pytest.fixture(scope="session", autouse=True)
def ensure_validation_enabled_once(sunbeam_client) -> None:
    """Enable the validation feature once for all chaos tests.

    Chaos scenarios assume that the validation feature is enabled and they
    merely start ``sunbeam validation run smoke`` during fault injection.
    """
    logger.info("Ensuring 'validation' feature is enabled for chaos tests...")
    try:
        sunbeam_client.enable_feature("validation")
    except (
        subprocess.CalledProcessError
    ) as exc:  # pragma: no cover - environment-specific
        logger.warning(
            "Validation feature could not be enabled; chaos tests may fail: %s",
            exc,
        )
