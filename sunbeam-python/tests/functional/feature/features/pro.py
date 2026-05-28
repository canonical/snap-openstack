# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for pro feature.

Ubuntu Pro integrates subscription/entitlement with the deployment.
Functionality is minimally validated via a generic OpenStack service call.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class ProTest(BaseFeatureTest):
    """Test pro feature enablement/disablement."""

    feature_name = "pro"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def __init__(self, *args, **kwargs) -> None:
        """Initialise Pro test with a token argument for enable.

        The token is taken from the functional test configuration, if present.
        If no token is configured, a dummy placeholder is used.
        """
        super().__init__(*args, **kwargs)
        pro_cfg = self.config.get("pro", {}) if self.config is not None else {}
        token = pro_cfg.get("token", "DUMMY-UBUNTU-PRO-TOKEN")
        self.enable_args = ["--token", token]

    def verify_validate_feature_behavior(self) -> None:
        """Validate that OpenStack APIs remain reachable under Pro."""
        logger.info("Verifying OpenStack service catalog for Ubuntu Pro...")
        try:
            result = subprocess.run(
                ["openstack", "service", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to list services: %s", exc.stderr)
            raise AssertionError(
                f"OpenStack service catalog not accessible: {exc.stderr}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying OpenStack services: %s", exc)
            raise AssertionError(
                f"Ubuntu Pro feature verification failed: {exc}"
            ) from exc

        if not result.stdout.strip():
            raise AssertionError("Service list returned no data")

        logger.info("OpenStack service catalog verified via `openstack service list`")
