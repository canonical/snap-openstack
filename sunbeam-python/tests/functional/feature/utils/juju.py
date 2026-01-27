# SPDX-FileCopyrightText: 2024 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

"""Juju CLI wrapper using Jubilant library for feature functional tests."""

import json
import logging
from typing import Dict, List, Optional, Set

from jubilant import Juju

logger = logging.getLogger(__name__)


class JujuClient:
    """Client for interacting with Juju using Jubilant."""

    def __init__(self, model: str = "openstack", controller: Optional[str] = None):
        self.model = model
        self.controller = controller
        self._juju: Optional[Juju] = None

    @property
    def juju(self) -> Juju:
        """Get or create Jubilant Juju instance."""
        if self._juju is None:
            self._juju = Juju()
            if self.model:
                try:
                    self._juju.cli("switch", self.model)
                except Exception as exc:  # noqa: BLE001
                    # Log but continue - the model might already be active
                    logger.debug("Could not switch to model %s: %s", self.model, exc)
        return self._juju

    def is_connected(self) -> bool:
        """Check if we can connect to Juju."""
        result = self.juju.cli("status", "--format", "json")
        return bool(result)

    def get_applications(self) -> Set[str]:
        """Get list of all applications in the model."""
        result_str = self.juju.cli("status", "--format", "json")
        status = json.loads(result_str)
        applications: Set[str] = set()

        if "applications" in status:
            applications.update(status["applications"].keys())

        return applications

    def get_units(self) -> Set[str]:
        """Get list of all units in the model."""
        result_str = self.juju.cli("status", "--format", "json")
        status = json.loads(result_str)
        units: Set[str] = set()

        if "applications" in status:
            for app_data in status["applications"].values():
                if "units" in app_data:
                    for unit_name in app_data["units"].keys():
                        units.add(unit_name)

        return units

    def has_application(self, application_name: str) -> bool:
        """Check if an application exists."""
        applications = self.get_applications()
        return application_name in applications

    def has_unit(self, unit_name: str) -> bool:
        """Check if a unit exists."""
        units = self.get_units()
        return unit_name in units

    def wait_for_application(self, application_name: str, timeout: int = 300) -> bool:
        """Wait for an application to appear using Jubilant's wait mechanism."""
        if self.has_application(application_name):
            logger.info(
                "Application '%s' already exists, skipping wait",
                application_name,
            )
            return True

        def app_exists(status) -> bool:
            return hasattr(status, "apps") and application_name in status.apps

        self.juju.wait(app_exists, timeout=timeout, delay=1.0)
        return True

    def wait_for_unit(self, unit_name: str, timeout: int = 300) -> bool:
        """Wait for a unit to appear using Jubilant's wait mechanism."""
        if self.has_unit(unit_name):
            logger.info("Unit '%s' already exists, skipping wait", unit_name)
            return True

        def unit_exists(status) -> bool:
            if not hasattr(status, "apps"):
                return False
            for app_data in status.apps.values():
                if hasattr(app_data, "units") and unit_name in app_data.units:
                    return True
            return False

        self.juju.wait(unit_exists, timeout=timeout, delay=1.0)
        return True

    def wait_for_application_ready(
        self,
        application_name: str,
        timeout: int = 600,
    ) -> bool:
        """Wait for an application to be in 'active' state."""

        def app_active(status) -> bool:
            if not hasattr(status, "apps") or application_name not in status.apps:
                return False
            app = status.apps[application_name]
            return hasattr(app, "app_status") and app.app_status.current == "active"

        self.juju.wait(app_active, timeout=timeout, delay=1.0)
        return True

    def verify_applications_exist(
        self,
        expected_applications: List[str],
    ) -> Dict[str, bool]:
        """Verify that expected applications exist."""
        actual_applications = self.get_applications()
        results: Dict[str, bool] = {}

        for app in expected_applications:
            results[app] = app in actual_applications

        return results

    def verify_units_exist(self, expected_units: List[str]) -> Dict[str, bool]:
        """Verify that expected units exist."""
        actual_units = self.get_units()
        results: Dict[str, bool] = {}

        for unit in expected_units:
            results[unit] = unit in actual_units

        return results
