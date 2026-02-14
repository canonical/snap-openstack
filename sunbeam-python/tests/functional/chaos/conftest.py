# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Functional fixtures and config hooks for chaos tests."""

import logging

import pytest

from tests.functional.chaos.utils import _kubectl_command
from tests.functional.feature import conftest as feature_conftest  # noqa: F401

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def ensure_chaos_mesh_installed() -> None:
    """Ensure Chaos Mesh is installed and ready for chaos tests.

    This follows the documented Helm installation path, using:

    - sudo snap install helm --classic
    - helm repo add chaos-mesh https://charts.chaos-mesh.org
    - helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh
    """
    import subprocess

    def _has_chaos_mesh() -> bool:
        try:
            result = subprocess.run(
                _kubectl_command(["get", "pods", "-n", "chaos-mesh"]),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return False
        return "chaos-mesh" in result.stdout or "controller-manager" in result.stdout

    if _has_chaos_mesh():
        logger.info("Chaos Mesh already present in namespace 'chaos-mesh'.")
        return

    logger.info("Chaos Mesh not detected; attempting to install via Helm...")

    try:
        subprocess.run(
            ["helm", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        logger.info("Helm not found or not working; installing helm snap...")
        subprocess.run(
            ["sudo", "snap", "install", "helm", "--classic"],
            check=True,
            text=True,
        )

    subprocess.run(
        ["helm", "repo", "add", "chaos-mesh", "https://charts.chaos-mesh.org"],
        check=False,
        text=True,
    )
    subprocess.run(
        ["helm", "repo", "update"],
        check=False,
        text=True,
    )

    subprocess.run(
        [
            "helm",
            "upgrade",
            "--install",
            "chaos-mesh",
            "chaos-mesh/chaos-mesh",
            "--namespace",
            "chaos-mesh",
            "--create-namespace",
        ],
        check=True,
        text=True,
    )

    if not _has_chaos_mesh():
        raise RuntimeError(
            "Chaos Mesh could not be verified as running in 'chaos-mesh' "
            "namespace after attempted installation. "
            "Please check Juju/Helm/kubectl connectivity."
        )


@pytest.fixture(scope="session", autouse=True)
def ensure_validation_enabled_once(
    sunbeam_client,  # type: ignore[reportUnusedFunction]
) -> None:
    """Enable the validation feature once for all chaos tests.

    Chaos scenarios assume that the validation feature is enabled and they
    merely start ``sunbeam validation run smoke`` during fault injection.
    """
    import subprocess

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
