# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for dns feature.

DNS requires nameservers as arguments, so we use dummy nameservers for testing.
DNS is a simple feature with no direct feature dependencies (besides the required
nameservers argument). Functionality is validated via the Designate (DNS) API.
"""

import logging

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class DnsTest(BaseFeatureTest):
    """Test dns feature enablement/disablement."""

    feature_name = "dns"
    # DNS requires nameservers argument - using dummy values for testing
    enable_args: list[str] = ["ns1.example.com.", "ns2.example.com."]
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that DNS as a Service is reachable.

        We call `sunbeam dns address` to confirm that the
        Designate service is registered and accessible.
        """
        logger.info("Verifying DNS service endpoints are available...")
        try:
            self.sunbeam.run(["dns", "address"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying DNS service: %s", exc)
            raise AssertionError(f"DNS service verification failed: {exc}") from exc

        logger.info("DNS service endpoints verified via `sunbeam dns address`")
