# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import subprocess

import pytest

from sunbeam.commands.prepare_node import (
    BOOTSTRAP_TEMPLATE,
    COMMON_TEMPLATE,
    PREPARE_NODE_TEMPLATE,
)
from sunbeam.versions import SUPPORTED_RELEASE


def run_script(script, tmp_path, install_status):
    return subprocess.run(
        ["bash"],
        input=script,
        text=True,
        capture_output=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "INSTALL_STATUS": str(install_status),
        },
        check=False,
    )


@pytest.mark.parametrize("install_status", [0, 42])
def test_juju_install_status(tmp_path, install_status):
    script = (
        """
command() { if [ "$1 $2" = '-v juju' ]; then return 1; fi; builtin command "$@"; }
sudo() {
    if [ "$1 $2" = 'snap install' ]; then return "$INSTALL_STATUS"; fi
    echo "sudo $*"
}
snap() { echo 'openstack 1 1 2026.1/stable'; }
"""
        + COMMON_TEMPLATE
    )
    result = run_script(script, tmp_path, install_status)
    assert result.returncode == install_status
    assert ("snap refresh --hold openstack" in result.stdout) == (install_status == 0)


@pytest.mark.parametrize("install_status", [0, 42])
def test_dependency_install_status(tmp_path, install_status):
    script = (
        f"""
lsb_release() {{ echo '{SUPPORTED_RELEASE}'; }}
id() {{ echo 1000; }}
whoami() {{ echo ubuntu; }}
dpkg() {{ return 1; }}
sudo() {{
    if [ "$1" = 'grep' ]; then echo 'ubuntu ALL=(ALL) NOPASSWD:ALL'; return 0; fi
    if [ "$1 $2" = 'apt install' ]; then return "$INSTALL_STATUS"; fi
    if [ "$1" = 'usermod' ]; then echo 'reached usermod'; exit 0; fi
    echo "sudo $*"
}}
"""
        + PREPARE_NODE_TEMPLATE
    )
    result = run_script(script, tmp_path, install_status)
    assert result.returncode == install_status
    assert ("reached usermod" in result.stdout) == (install_status == 0)


@pytest.mark.parametrize("install_status", [0, 42])
def test_lxd_install_status(tmp_path, install_status):
    script = (
        """
whoami() { echo ubuntu; }
sudo() {
    if [ "$1 $2 $3" = 'snap install lxd' ]; then return "$INSTALL_STATUS"; fi
    if [ "$1" = 'usermod' ]; then echo 'reached usermod'; exit 0; fi
    echo "sudo $*"
}
"""
        + BOOTSTRAP_TEMPLATE
    )
    result = run_script(script, tmp_path, install_status)
    assert result.returncode == install_status
    assert ("reached usermod" in result.stdout) == (install_status == 0)
