# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Base class for Sunbeam feature functional tests."""

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..utils.juju import JujuClient
from ..utils.sunbeam import SunbeamClient

logger = logging.getLogger(__name__)


class BaseFeatureTest:
    """Base class for testing Sunbeam features."""

    feature_name: str = ""
    expected_units: List[str] = []
    expected_applications: List[str] = []
    timeout_seconds: int = 300
    enable_args: List[str] = []
    disable_args: List[str] = []

    def __init__(
        self,
        sunbeam_client: SunbeamClient,
        juju_client: JujuClient,
        config: Optional[Dict] = None,
    ):
        self.sunbeam = sunbeam_client
        self.juju = juju_client
        self.config = config or {}

        feature_config = self.config.get("features", {}).get(self.feature_name, {})
        self.expected_units = feature_config.get("expected_units", self.expected_units)
        self.expected_applications = feature_config.get(
            "expected_applications",
            self.expected_applications,
        )
        self.timeout_seconds = feature_config.get(
            "timeout_seconds",
            self.timeout_seconds,
        )
        self.enable_args = feature_config.get("enable_args", self.enable_args)
        self.disable_args = feature_config.get("disable_args", self.disable_args)

        self._ensure_openstack_env()

    def enable(self) -> bool:
        """Enable the feature."""
        logger.info("Enabling feature: '%s'", self.feature_name)
        return self.sunbeam.enable_feature(
            self.feature_name,
            extra_args=self.enable_args,
        )

    def disable(self) -> bool:
        """Disable the feature.

        Returns True if successful, False otherwise.
        """
        logger.info("Disabling feature: '%s'", self.feature_name)
        try:
            return self.sunbeam.disable_feature(
                self.feature_name,
                extra_args=self.disable_args,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to disable feature '%s': %s",
                self.feature_name,
                exc,
            )
            return False

    def run_full_lifecycle(self) -> bool:
        """Run enable/disable lifecycle with timing.

        Disable failures are logged but do not fail the overall test.
        """
        logger.info("Starting lifecycle test for feature: '%s'", self.feature_name)

        enable_start = time.time()
        logger.info("[ENABLE] Starting enable for '%s'...", self.feature_name)
        enable_success = self.enable()
        enable_duration = time.time() - enable_start
        if enable_success:
            logger.info(
                "[ENABLE] SUCCESS for '%s' - Time taken: %.2f seconds",
                self.feature_name,
                enable_duration,
            )
        else:
            logger.error(
                "[ENABLE] FAILED for '%s' - Time taken: %.2f seconds",
                self.feature_name,
                enable_duration,
            )
            return False

        try:
            self.verify_validate_feature_behavior()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Validation failed for feature '%s' after enable", self.feature_name
            )
            # Best-effort cleanup – if disable also fails, log and continue.
            try:
                self.disable()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Disable also failed while handling validation error for '%s'",
                    self.feature_name,
                )
            return False

        disable_start = time.time()
        logger.info("[DISABLE] Starting disable for '%s'...", self.feature_name)
        disable_success = self.disable()
        disable_duration = time.time() - disable_start
        if disable_success:
            logger.info(
                "[DISABLE] SUCCESS for '%s' - Time taken: %.2f seconds",
                self.feature_name,
                disable_duration,
            )
        else:
            logger.warning(
                "[DISABLE] FAILED for '%s' - Time taken: %.2f seconds (continuing anyway)",
                self.feature_name,
                disable_duration,
            )

        total_duration = time.time() - enable_start
        logger.info(
            "[SUMMARY] Feature '%s' - Enable: %.2fs, Disable: %.2fs (%s), Total: %.2fs",
            self.feature_name,
            enable_duration,
            disable_duration,
            "SUCCESS" if disable_success else "FAILED",
            total_duration,
        )
        return True

    def verify_enabled(self) -> None:
        """Verify that expected applications and units are present.

        This is a boilerplate method for future use. Currently not called
        by default, but can be overridden in subclasses to add verification.
        """
        pass

    def validate_feature_behavior(self) -> None:
        """Validate that the feature is working correctly.

        This is a boilerplate method for future use. Currently not called
        by default, but can be overridden in subclasses to add functionality tests.
        """
        pass

    def verify_validate_feature_behavior(self) -> None:
        """Simple verification that feature is enabled and basic check passes.

        This is a simple method that can be called after enable to verify
        the feature is working. Override in subclasses for feature-specific checks.
        """
        logger.info("Verifying feature '%s' is enabled...", self.feature_name)
        if self.expected_applications:
            for app in self.expected_applications:
                if self.juju.has_application(app):
                    logger.info("Application '%s' found", app)
                else:
                    logger.warning(
                        "Application '%s' not found (may still be deploying)", app
                    )
        logger.info("Basic verification completed for feature '%s'", self.feature_name)

    def _ensure_openstack_env(self) -> None:
        """Load OpenStack credentials from adminrc if needed.

        This avoids repeating sourcing logic across tests and keeps credentials
        out of the code. If OS_AUTH_URL is already set, this is a no-op.
        """
        if os.environ.get("OS_AUTH_URL"):
            return

        adminrc_path = Path(__file__).resolve().parent / "adminrc"
        if not adminrc_path.exists():
            logger.debug(
                "adminrc file not found at %s; relying on existing environment",
                adminrc_path,
            )
            return

        try:
            for line in adminrc_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("export "):
                    continue
                _, rest = line.split("export ", 1)
                if "=" not in rest:
                    continue
                key, value = rest.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
            logger.info("Loaded OpenStack credentials from %s", adminrc_path)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to load OpenStack credentials from %s",
                adminrc_path,
            )
