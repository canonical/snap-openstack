# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for telemetry feature.

Telemetry is a simple feature with no dependencies.
Deploys Ceilometer, Aodh, Gnocchi, and OpenStack Exporter.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class TelemetryTest(BaseFeatureTest):
    """Test telemetry feature enablement/disablement."""

    feature_name = "telemetry"
    expected_applications: list[str] = ["ceilometer", "gnocchi", "aodh"]
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that telemetry services are working."""
        logger.info("Verifying telemetry services are available...")

        # Check if alarm service (Aodh) is accessible
        try:
            result = subprocess.run(
                ["openstack", "alarm", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            logger.info("Telemetry alarm service (Aodh) is accessible")
            logger.debug("Alarm list output: %s", result.stdout[:200])

        except Exception as e:
            logger.warning("Error checking telemetry services: %s", e)
            raise AssertionError(f"Telemetry service verification failed: {e}")
