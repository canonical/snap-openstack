# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for caas feature.

Container as a Service (Magnum) allows managing Kubernetes clusters via OpenStack.
Functionality is validated via the Magnum (COE) API.
"""

import logging
import subprocess

import pytest

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class CaaSTest(BaseFeatureTest):
    """Test caas feature enablement/disablement."""

    feature_name = "caas"
    expected_units: list[str] = []
    expected_applications: list[str] = []
    timeout_seconds = 600

    def _ensure_dependency_enabled(self, feature: str) -> bool:
        """Best-effort enable a required dependency feature.

        If enabling the dependency fails (for example, missing Vault for
        Secrets), we treat this as an unsatisfied dependency and skip.
        """
        logger.info("Ensuring dependency feature '%s' is enabled for CaaS...", feature)
        try:
            self.sunbeam.enable_feature(feature)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to enable dependency '%s' required by CaaS: %s",
                feature,
                exc,
            )
            return False
        return True

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Magnum (COE) API is reachable.

        We call `openstack coe cluster list` to confirm the API is up.
        """
        logger.info("Verifying CaaS (Magnum) service is available...")
        try:
            subprocess.run(
                ["openstack", "coe", "cluster", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to list COE clusters: %s", exc.stderr)
            raise AssertionError(
                f"CaaS (Magnum) service not accessible: {exc.stderr}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying CaaS service: %s", exc)
            raise AssertionError(f"CaaS service verification failed: {exc}") from exc

        logger.info("CaaS (Magnum) service verified via `openstack coe cluster list`")

    def run_full_lifecycle(self) -> bool:
        """Ensure dependencies then run the standard enable/verify/disable flow.

        CaaS depends on the Secrets and Load Balancer features.
        """
        for dep in ("secrets", "loadbalancer"):
            if not self._ensure_dependency_enabled(dep):
                pytest.skip(
                    f"Skipping CaaS feature test: dependency '{dep}' "
                    "could not be enabled"
                )

        return super().run_full_lifecycle()
