# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Test for ldap feature.

LDAP integration configures Keystone to authenticate against LDAP.
Functionality is minimally validated via the Identity API.
"""

import logging
import subprocess

from .base import BaseFeatureTest

logger = logging.getLogger(__name__)


class LdapTest(BaseFeatureTest):
    """Test ldap feature enablement/disablement."""

    feature_name = "ldap"
    expected_applications: list[str] = []
    timeout_seconds = 600

    def verify_validate_feature_behavior(self) -> None:
        """Validate that the Identity API is reachable."""
        logger.info("Verifying Identity (Keystone) service is available for LDAP...")
        try:
            subprocess.run(
                ["openstack", "domain", "list"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error while verifying Identity service: %s", exc)
            raise AssertionError(f"LDAP feature verification failed: {exc}") from exc

        logger.info("Identity service verified via `openstack domain list`")
