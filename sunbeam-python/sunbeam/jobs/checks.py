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

import logging
import os
import re
from pathlib import Path

from snaphelpers import Snap, SnapCtl

from sunbeam.clusterd.client import Client
from sunbeam.jobs.common import (
    RAM_16_GB_IN_KB,
    get_host_total_cores,
    get_host_total_ram,
)

LOG = logging.getLogger(__name__)


class Check:
    """Base class for Pre-flight checks.

    Check performs a verification step to determine
    to proceed further or not.
    """

    def __init__(self, name: str, description: str = ""):
        """Initialise the Check.

        :param name: the name of the check
        """
        self.name = name
        self.description = description
        self.message = None

    def run(self) -> bool:
        """Run the check logic here.

        Return True if check is Ok.
        Otherwise update self.message and return False.
        """

        return True


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
    """Check if ~/.local/share exists for Juju use."""

    def __init__(self):
        super().__init__(
            "Check for .local/share directory",
            "Checking for ~/.local/share directory for Juju",
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

    def __init__(self):
        super().__init__(
            "Check bootstrapped",
            "Checking the deployment has been bootstrapped",
        )
        self.client = Client()

    def run(self) -> bool:
        bootstrapped = self.client.cluster.check_sunbeam_bootstrapped()
        if bootstrapped:
            return True
        else:
            self.message = (
                "Deployment not bootstrapped or bootstrap process has not "
                "completed succesfully. Please run `sunbeam cluster bootstrap`"
            )
            return False
