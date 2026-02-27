# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for resource-optimization feature.

Resource Optimization provides Watcher as a service.
Functionality is validated via the Watcher (optimize) API.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class ResourceOptimizationTest(BaseFeatureTest):
    """Test resource-optimization feature enablement/disablement."""

    feature_name = "resource-optimization"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Watcher (resource optimization) API is reachable.

        We call `openstack optimize goal list` to confirm the API is up.
        """
        logger.info("Verifying Resource Optimization (Watcher) service is available...")
        try:
            subprocess.run(
                ["openstack", "optimize", "goal", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Error while verifying Resource Optimization service: %s",
                exc,
            )
            raise AssertionError(
                f"Resource Optimization service verification failed: {exc}"
            ) from exc

        logger.info(
            "Resource Optimization service verified via `openstack optimize goal list`"
        )
