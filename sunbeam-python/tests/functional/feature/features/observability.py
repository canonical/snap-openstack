# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for observability feature.

Observability integrates Canonical OpenStack with COS.

For this functional test we exercise the simple embedded workflow from the
documentation:

1. `sunbeam enable observability embedded`
2. `sunbeam observability dashboard-url`
3. `sunbeam disable observability embedded`
"""

import logging

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class ObservabilityTest(BaseFeatureTest):
    """Test observability feature enablement/disablement."""

    feature_name = "observability"
    enable_args: list[str] = ["embedded"]
    disable_args: list[str] = ["embedded"]
    expected_applications: list[str] = []
    timeout_seconds = 900

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the observability dashboard URL is available.

        This uses `sunbeam observability dashboard-url` from the docs to
        confirm that the embedded COS deployment is responding.
        """
        logger.info("Fetching observability dashboard URL...")
        try:
            result = self.sunbeam.run(["observability", "dashboard-url"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Error while retrieving observability dashboard URL: %s",
                exc,
            )
            raise AssertionError(
                f"Observability feature verification failed: {exc}"
            ) from exc

        url = result.stdout.strip()
        logger.info("Observability dashboard URL: %s", url)
