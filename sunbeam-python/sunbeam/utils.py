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

import glob
import logging
import socket
import sys
from pathlib import Path
from typing import Dict, List

import click
import netifaces
import pwgen
from pyroute2 import IPDB, NDB

LOG = logging.getLogger(__name__)
LOCAL_ACCESS = "local"
REMOTE_ACCESS = "remote"


def is_nic_connected(iface_name: str) -> bool:
    """Check if nic is physically connected."""
    with IPDB() as ipdb:
        state = ipdb.interfaces[iface_name].operstate
        # pyroute2 does not seem to expose the states as
        # consumable constants
        return state == "UP"


def is_nic_up(iface_name: str) -> bool:
    """Check if nic is up."""
    with NDB() as ndb:
        state = ndb.interfaces[iface_name]["state"]
        return state.upper() == "UP"


def get_fqdn() -> str:
    """Get FQDN of the machine"""
    return socket.getfqdn()


def get_local_ip_by_default_route() -> str:
    """Get IP address of host associated with default gateway"""
    interface = "lo"
    ip = "127.0.0.1"

    # TOCHK: Gathering only IPv4
    if "default" in netifaces.gateways():
        interface = netifaces.gateways()["default"][netifaces.AF_INET][1]

    ip_list = netifaces.ifaddresses(interface)[netifaces.AF_INET]
    if len(ip_list) > 0 and "addr" in ip_list[0]:
        ip = ip_list[0]["addr"]

    return ip


def get_nic_macs(nic: str) -> list:
    """Return list of mac addresses associates with nic."""
    addrs = netifaces.ifaddresses(nic)
    return sorted([a["addr"] for a in addrs[netifaces.AF_LINK]])


def filter_link_local(addresses: List[Dict]) -> List[Dict]:
    """Filter any IPv6 link local addresses from configured IPv6 addresses."""
    if addresses is None:
        return None
    return [addr for addr in addresses if "fe80" not in addr.get("addr")]


def is_configured(nic: str) -> bool:
    """Whether interface is configured with IPv4 or IPv6 address."""
    addrs = netifaces.ifaddresses(nic)
    return bool(
        addrs.get(netifaces.AF_INET) or filter_link_local(addrs.get(netifaces.AF_INET6))
    )


def get_free_nics(include_configured=False) -> list:
    """Return a list of nics which doe not have a v4 or v6 address."""
    virtual_nic_dir = "/sys/devices/virtual/net/*"
    virtual_nics = [Path(p).name for p in glob.glob(virtual_nic_dir)]
    bond_nic_dir = "/sys/devices/virtual/net/*/bonding"
    bonds = [Path(p).parent.name for p in glob.glob(bond_nic_dir)]
    bond_macs = []
    for bond_iface in bonds:
        bond_macs.extend(get_nic_macs(bond_iface))
    candidate_nics = []
    for nic in netifaces.interfaces():
        if nic in bonds and not is_configured(nic):
            LOG.debug(f"Found bond {nic}")
            candidate_nics.append(nic)
            continue
        macs = get_nic_macs(nic)
        if list(set(macs) & set(bond_macs)):
            LOG.debug(f"Skipping {nic} it is part of a bond")
            continue
        if nic in virtual_nics:
            LOG.debug(f"Skipping {nic} it is virtual")
            continue
        if is_configured(nic) and not include_configured:
            LOG.debug(f"Skipping {nic} it is configured")
        else:
            LOG.debug(f"Found nic {nic}")
            candidate_nics.append(nic)
    return candidate_nics


def get_free_nic() -> str:
    """Return a single candidate nic."""
    nics = get_free_nics()
    nic = ""
    if len(nics) > 0:
        nic = nics[0]
    return nic


def generate_password() -> str:
    """Generate a password."""
    return pwgen.pwgen(12)


class CatchGroup(click.Group):
    """Catch exceptions and print them to stderr."""

    def __call__(self, *args, **kwargs):
        try:
            return self.main(*args, **kwargs)
        except Exception as e:
            LOG.debug(e, exc_info=True)
            message = (
                "An unexpected error has occurred."
                " Please run 'sunbeam inspect' to generate an inspection report."
            )
            LOG.warn(message)
            LOG.error("Error: %s", e)
            sys.exit(1)
