# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for maintenance feature."""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class MaintenanceTest(BaseFeatureTest):
    """Test maintenance feature enablement/disablement."""

    feature_name = "maintenance"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Compute API is reachable."""
        logger.info("Verifying Compute service is available for maintenance...")
        try:
            subprocess.run(
                ["openstack", "compute", "service", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Compute service: %s", exc)
            raise AssertionError(
                f"Maintenance feature verification failed: {exc}"
            ) from exc

        logger.info("Compute service verified via `openstack compute service list`")
