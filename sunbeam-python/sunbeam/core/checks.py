# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import enum
import grp
import json
import logging
import os
import re
from pathlib import Path
from typing import Sequence

import click
from rich.console import Console
from snaphelpers import Snap, SnapCtl

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException
from sunbeam.core.common import (
    RAM_16_GB_IN_KB,
    get_host_total_cores,
    get_host_total_ram,
)
from sunbeam.core.juju import JujuStepHelper

LOG = logging.getLogger(__name__)


def run_preflight_checks(checks: Sequence["Check"], console: Console):
    """Run preflight checks sequentially.

    Runs each checks, logs whether the check passed or failed.
    Exits at first failure.

    Raise ClickException in case of Result Failures.
    """
    for check in checks:
        LOG.debug(f"Starting pre-flight check {check.name}")
        message = f"{check.description} ... "
        with console.status(message):
            if not check.run():
                raise click.ClickException(check.message)


class Check:
    """Base class for Pre-flight checks.

    Check performs a verification step to determine
    to proceed further or not.
    """

    name: str
    description: str
    message: str

    def __init__(self, name: str, description: str = ""):
        """Initialise the Check.

        :param name: the name of the check
        """
        self.name = name
        self.description = description
        self.message = "Check successful"

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """
        return True


class DiagnosticResultType(enum.Enum):
    """Enum for diagnostic result types."""

    SUCCESS = "success"
    WARNING = "warning"
    FAILURE = "failure"


class DiagnosticsResult:
    def __init__(
        self,
        name: str,
        passed: DiagnosticResultType,
        message: str | None = None,
        diagnostics: str | None = None,
        **details: dict,
    ):
        self.name = name
        self.passed = passed
        self.message = message
        self.diagnostics = diagnostics
        self.details = details

    def to_dict(self) -> dict:
        """Return the result as a dictionary."""
        result = {
            "name": self.name,
            "passed": self.passed.value,
            **self.details,
        }
        if self.message:
            result["message"] = self.message
        if self.diagnostics:
            result["diagnostics"] = self.diagnostics
        return result

    @classmethod
    def fail(
        cls,
        name: str,
        message: str | None = None,
        diagnostics: str | None = None,
        **details: dict,
    ):
        """Helper to create a failed result."""
        return cls(name, DiagnosticResultType.FAILURE, message, diagnostics, **details)

    @classmethod
    def success(
        cls,
        name: str,
        message: str | None = None,
        diagnostics: str | None = None,
        **details: dict,
    ):
        """Helper to create a successful result."""
        return cls(name, DiagnosticResultType.SUCCESS, message, diagnostics, **details)

    @classmethod
    def warn(
        cls,
        name: str,
        message: str | None = None,
        diagnostics: str | None = None,
        **details: dict,
    ):
        """Helper to create a warning result."""
        return cls(name, DiagnosticResultType.WARNING, message, diagnostics, **details)

    @staticmethod
    def coalesce_type(results: list["DiagnosticsResult"]) -> DiagnosticResultType:
        """Coalesce results into a single result."""
        types = [result.passed for result in results]
        if DiagnosticResultType.FAILURE in types:
            return DiagnosticResultType.FAILURE
        if DiagnosticResultType.WARNING in types:
            return DiagnosticResultType.WARNING
        return DiagnosticResultType.SUCCESS


class DiagnosticsCheck:
    """Base class for Diagnostics checks."""

    name: str
    description: str

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description

    def run(self) -> DiagnosticsResult | list[DiagnosticsResult]:
        """Run the check logic here.

        Return list of DiagnosticsResult.
        """
        raise NotImplementedError


class JujuSnapCheck(Check):
    """Check if juju snap is installed or not."""

    def __init__(self):
        super().__init__(
            "Check for juju snap",
            "Checking for presence of Juju",
        )

    def run(self) -> bool:
        """Check for juju-bin content."""
        snap = Snap()
        juju_content = snap.paths.snap / "juju"
        if not juju_content.exists():
            self.message = "Juju not detected: please install snap"

            return False

        return True


class SshKeysConnectedCheck(Check):
    """Check if ssh-keys interface is connected or not."""

    def __init__(self):
        super().__init__(
            "Check for ssh-keys interface",
            "Checking for presence of ssh-keys interface",
        )

    def run(self) -> bool:
        """Check for ssh-keys interface."""
        snap = Snap()
        snap_ctl = SnapCtl()
        connect = f"sudo snap connect {snap.name}:ssh-keys"

        if not snap_ctl.is_connected("ssh-keys"):
            self.message = (
                "ssh-keys interface not detected\n"
                "Please connect ssh-keys interface by running:\n"
                "\n"
                f"    {connect}"
                "\n"
            )
            return False

        return True


class DaemonGroupCheck(Check):
    """Check if user is member of socket group."""

    def __init__(self):
        snap = Snap()

        self.user = os.environ.get("USER")
        self.group = snap.config.get("daemon.group")
        self.clusterd_socket = Path(snap.paths.common / "state" / "control.socket")

        super().__init__(
            "Check for snap_daemon group membership",
            f"Checking if user {self.user} is member of group {self.group}",
        )

    def run(self) -> bool:
        """Return false if user is not member of group.

        Checks:
            - User has access to clusterd socket
        """
        if not os.access(self.clusterd_socket, os.W_OK):
            self.message = (
                "Insufficient permissions to run sunbeam commands\n"
                f"Add the user {self.user!r} to the {self.group!r} group:\n"
                "\n"
                f"    sudo usermod -a -G {self.group} {self.user}\n"
                "\n"
                "After this, reload the user groups either via a reboot or by"
                f" running 'newgrp {self.group}'."
            )

            return False

        return True


# NOTE: drop with Juju can do this itself
class LocalShareCheck(Check):
    """Check if ~/.local/share exists."""

    def __init__(self):
        super().__init__(
            "Check for .local/share directory",
            "Checking for ~/.local/share directory",
        )

    def run(self) -> bool:
        """Check for ~./local/share."""
        snap = Snap()

        local_share = snap.paths.real_home / ".local" / "share"
        if not os.path.exists(local_share):
            self.message = (
                f"{local_share} directory not detected\n"
                "Please create by running:\n"
                "\n"
                f"    mkdir -p {local_share}"
                "\n"
            )
            return False

        return True


class VerifyFQDNCheck(Check):
    """Check if FQDN is correct."""

    def __init__(self, fqdn: str):
        super().__init__(
            "Check for FQDN",
            "Checking for FQDN",
        )
        self.fqdn = fqdn

    def run(self) -> bool:
        """Return false if FQDN is not valid.

        Checks:
            - FQDN is not empty
            - FQDN is not longer than 255 characters
            - FQDN has at least one label
            - FQDN labels are not empty
            - FQDN labels are not longer than 63 characters
            - FQDN labels do not start or end with hyphen
            - FQDN labels contain only alphanumeric characters and hyphens
        """
        if not self.fqdn:
            self.message = "FQDN cannot be an empty string"
            return False

        if len(self.fqdn) > 255:
            self.message = (
                "A FQDN cannot be longer than 255 characters (trailing dot included)"
            )
            return False

        labels = self.fqdn.split(".")

        if len(labels) == 1:
            self.message = (
                "A FQDN must have at least one label and a trailing dot,"
                " or two labels separated by a dot"
            )
            return False

        if self.fqdn.endswith("."):
            # strip trailing dot
            del labels[-1]

        label_regex = re.compile(r"^[a-z0-9-]*$", re.IGNORECASE)

        for label in labels:
            if not 1 < len(label) < 63:
                self.message = (
                    "A label in a FQDN cannot be empty or longer than 63 characters"
                )
                return False

            if label.startswith("-") or label.endswith("-"):
                self.message = "A label in a FQDN cannot start or end with a hyphen (-)"
                return False

            if label_regex.match(label) is None:
                self.message = (
                    "A label in a FQDN can only contain alphanumeric characters"
                    " and hyphens (-)"
                )
                return False

        return True


class VerifyHypervisorHostnameCheck(Check):
    """Check if Hypervisor Hostname is same as FQDN."""

    def __init__(self, fqdn, hypervisor_hostname):
        super().__init__(
            "Check for Hypervisor Hostname",
            "Checking if Hypervisor Hostname is same as FQDN",
        )
        self.fqdn = fqdn
        self.hypervisor_hostname = hypervisor_hostname

    def run(self) -> bool:
        """Verify if Hypervisor Hostname is same as FQDN."""
        if self.fqdn == self.hypervisor_hostname:
            return True

        self.message = (
            "Host FQDN and Hypervisor hostname perceived by libvirt are different, "
            "check `hostname -f` and `/etc/hosts` file"
        )
        return False


class SystemRequirementsCheck(Check):
    """Check if machine has minimum 4 cores and 16GB RAM."""

    def __init__(self):
        super().__init__(
            "Check for system requirements",
            "Checking for host configuration of minimum 4 core and 16G RAM",
        )

    def run(self) -> bool:
        """Validate system requirements.

        Checks:
            - Host has enough RAM
            - Host has at least 4 cores
        """
        host_total_ram = get_host_total_ram()
        host_total_cores = get_host_total_cores()
        if host_total_ram < RAM_16_GB_IN_KB or host_total_cores < 4:
            self.message = (
                "WARNING: Minimum system requirements (4 core CPU, 16 GB RAM) not met."
            )
            LOG.warning(self.message)

        return True


class VerifyBootstrappedCheck(Check):
    """Check deployment has been bootstrapped."""

    def __init__(self, client: Client):
        super().__init__(
            "Check bootstrapped",
            "Checking the deployment has been bootstrapped",
        )
        self.client = client

    def run(self) -> bool:
        """Validate deployment has been bootstrapped.

        Checks:
            - Clusterd config key bootstrap is True
        """
        bootstrapped = self.client.cluster.check_sunbeam_bootstrapped()
        if bootstrapped:
            return True
        else:
            self.message = (
                "Deployment not bootstrapped or bootstrap process has not "
                "completed succesfully. Please run `sunbeam cluster bootstrap`"
            )
            return False


class VerifyClusterdNotBootstrappedCheck(Check):
    """Check deployment has not been bootstrapped."""

    def __init__(self):
        super().__init__(
            "Check internal database has not been bootstrapped",
            "Checking the internal database has not been bootstrapped",
        )
        self.client = Client.from_socket()

    def run(self) -> bool:
        """Verify local instance of clusterd is not bootstrapped.

        Checks:
            - any key returns ClusterServiceUnavailableException
        """
        try:
            self.client.cluster.get_config("any")
        except ClusterServiceUnavailableException:
            return True
        except Exception:
            LOG.debug("Failed to check if clusterd is bootstrapped", exc_info=True)

        self.message = (
            "Local deployment has already been bootstrapped,"
            " which is only compatible in a local type deployment."
        )
        return False


class TokenCheck(Check):
    """Check if a join token looks valid."""

    def __init__(self, token: str):
        super().__init__(
            "Check for valid join token",
            "Checking if join token looks valid",
        )
        self.token = token

    def run(self) -> bool:
        """Return false if the token provided is not valid.

        Checks:
            - Token is a valid base64 string
            - Token content is a valid JSON object
            - Token contains the required fields
            - Token 'name' matches the hostname
            - Token 'join_addresses' is a list and not empty
        """
        if not self.token:
            self.message = "Join token cannot be an empty string"
            return False

        try:
            token_bytes = base64.b64decode(self.token)
        except Exception:
            LOG.exception("Failed to decode join token")
            self.message = "Join token is not a valid base64 string"
            return False

        try:
            token = json.loads(token_bytes)
        except Exception:
            LOG.exception("Failed to decode join token")
            self.message = "Join token content is not a valid JSON-encoded object"
            return False

        if not isinstance(token, dict):
            self.message = "Join token content is not a valid JSON object"
            return False

        missing_keys = {"secret", "join_addresses", "fingerprint"} - set(token.keys())

        if missing_keys:
            self.message = "Join token does not contain the following required fields: "
            self.message += ", ".join(sorted(missing_keys))

            return False

        join_addresses = token["join_addresses"]
        if not isinstance(join_addresses, list):
            self.message = "Join token 'join_addresses' is not a list"
            return False
        if len(join_addresses) == 0:
            self.message = "Join token 'join_addresses' is empty"
            return False

        return True


class JujuControllerRegistrationCheck(Check):
    """Check if juju controller is registered or not."""

    def __init__(self, controller: str, data_location: Path):
        super().__init__(
            "Check Juju Controller registration",
            "Checking if juju controller is registered",
        )
        self.controller = controller
        self.data_location = data_location

    def run(self) -> bool:
        """Validate registration of juju controller.

        Checks:
            - Existence of accounts file for juju controller
        """
        account_file = self.data_location / f"{self.controller}.yaml"
        if account_file.exists():
            return True
        else:
            self.message = f"Juju controller {self.controller} is not registered."
            return False


class LxdGroupCheck(Check):
    """Check if user is member of lxd group."""

    def __init__(self):
        self.user = os.environ.get("USER")
        self.group = "lxd"

        super().__init__(
            "Check for lxd group membership",
            f"Checking if user {self.user} is member of group {self.group}",
        )

    def run(self) -> bool:
        """Return false if user is not member of group.

        Checks:
            - User is part of group
        """
        if self.user not in grp.getgrnam(self.group).gr_mem:
            self.message = (
                f"{self.user!r} not part of lxd group"
                "Insufficient permissions to run sunbeam commands\n"
                f"Add the user {self.user!r} to the {self.group!r} group:\n"
                "\n"
                f"    sudo usermod -a -G {self.group} {self.user}\n"
                "\n"
                "After this, reload the user groups either via a reboot or by"
                f" running 'newgrp {self.group}'."
            )

            return False

        return True


class LXDJujuControllerRegistrationCheck(Check):
    """Check if lxd juju controller exists."""

    def __init__(self):
        super().__init__(
            "Check existence of LXD Juju Controller",
            "Checking if lxd juju controller exists",
        )

    def run(self) -> bool:
        """Check if lxd juju controller exists."""
        controllers = JujuStepHelper().get_controllers(clouds=["localhost"])
        if len(controllers) == 0:
            self.message = (
                "Missing Juju controller on LXD"
                "Bootstrap Juju controller on LXD:"
                "\n"
                "    juju bootstrap localhost"
                "\n"
            )
            return False

        return True
