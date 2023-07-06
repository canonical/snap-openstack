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
import ipaddress
import logging
import re
import socket
import sys
from pathlib import Path
from typing import Dict, List, Optional

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


def get_hypervisor_hostname() -> str:
    """Get FQDN as per libvirt."""
    # Use same logic used by libvirt
    # https://github.com/libvirt/libvirt/blob/a5bf2c4bf962cfb32f9137be5f0ba61cdd14b0e7/src/util/virutil.c#L406
    hostname = socket.gethostname()
    if "." in hostname:
        return hostname

    addrinfo = socket.getaddrinfo(
        hostname, None, family=socket.AF_UNSPEC, flags=socket.AI_CANONNAME
    )
    for addr in addrinfo:
        fqdn = addr[3]
        if fqdn and fqdn != "localhost":
            return fqdn

    return hostname


def get_fqdn() -> str:
    """Get FQDN of the machine"""
    # If the fqdn returned by this function and from libvirt are different,
    # the hypervisor name and the one registered in OVN will be different
    # which leads to port binding errors,
    # see https://bugs.launchpad.net/snap-openstack/+bug/2023931

    fqdn = get_hypervisor_hostname()
    if "." in fqdn:
        return fqdn

    # Deviation from libvirt logic
    # Try to get fqdn from IP address as a last resort
    ip = get_local_ip_by_default_route()
    try:
        fqdn = socket.getfqdn(socket.gethostbyaddr(ip)[0])
        if fqdn != "localhost":
            return fqdn
    except Exception as e:
        LOG.debug("Ignoring error in getting FQDN")
        LOG.debug(e, exc_info=True)

    # return hostname if fqdn is localhost
    return socket.gethostname()


def _get_default_gw_iface_fallback() -> Optional[str]:
    """Returns the default gateway interface.

    Parses the /proc/net/route table to determine the interface with a default
    route. The interface with the default route will have a destination of 0x000000,
    a mask of 0x000000 and will have flags indicating RTF_GATEWAY and RTF_UP.

    :return Optional[str, None]: the name of the interface the default gateway or
            None if one cannot be found.
    """
    # see include/uapi/linux/route.h in kernel source for more explanation
    RTF_UP = 0x1  # noqa - route is usable
    RTF_GATEWAY = 0x2  # noqa - destination is a gateway

    iface = None
    with open("/proc/net/route", "r") as f:
        contents = [line.strip() for line in f.readlines() if line.strip()]
        print(contents)

        entries = []
        # First line is a header line of the table contents. Note, we skip blank entries
        # by default there's an extra column due to an extra \t character for the table
        # contents to line up. This is parsing the /proc/net/route and creating a set of
        # entries. Each entry is a dict where the keys are table header and the values
        # are the values in the table rows.
        header = [col.strip().lower() for col in contents[0].split("\t") if col]
        for row in contents[1:]:
            cells = [col.strip() for col in row.split("\t") if col]
            entries.append(dict(zip(header, cells)))

        def is_up(flags: str) -> bool:
            return int(flags, 16) & RTF_UP == RTF_UP

        def is_gateway(flags: str) -> bool:
            return int(flags, 16) & RTF_GATEWAY == RTF_GATEWAY

        # Check each entry to see if it has the default gateway. The default gateway
        # will have destination and mask set to 0x00, will be up and is noted as a
        # gateway.
        for entry in entries:
            if int(entry.get("destination", 0xFF), 16) != 0:
                continue
            if int(entry.get("mask", 0xFF), 16) != 0:
                continue
            flags = entry.get("flags", 0x00)
            if is_up(flags) and is_gateway(flags):
                iface = entry.get("iface", None)
                break

    return iface


def get_ifaddresses_by_default_route() -> dict:
    """Get address configuration from interface associated with default gateway."""
    interface = "lo"
    ip = "127.0.0.1"
    netmask = "255.0.0.0"

    # TOCHK: Gathering only IPv4
    default_gateways = netifaces.gateways().get("default", {})
    if default_gateways and netifaces.AF_INET in default_gateways:
        interface = netifaces.gateways()["default"][netifaces.AF_INET][1]
    else:
        # There are some cases where netifaces doesn't return the machine's default
        # gateway, but it does exist. Let's check the /proc/net/route table to see
        # if we can find the proper gateway.
        interface = _get_default_gw_iface_fallback() or "lo"

    ip_list = netifaces.ifaddresses(interface)[netifaces.AF_INET]
    if len(ip_list) > 0 and "addr" in ip_list[0]:
        return ip_list[0]

    return {"addr": ip, "netmask": netmask}


def get_local_ip_by_default_route() -> str:
    """Get IP address of host associated with default gateway."""
    return get_ifaddresses_by_default_route()["addr"]


def get_local_cidr_by_default_routes() -> str:
    """Get CIDR of host associated with default gateway"""
    conf = get_ifaddresses_by_default_route()
    ip = conf["addr"]
    netmask = conf["netmask"]
    network = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
    return str(network)


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


def get_nameservers(ipv4_only=True) -> List[str]:
    """Return a list of nameservers used by the host."""
    resolve_config = Path("/run/systemd/resolve/resolv.conf")
    nameservers = []
    try:
        with open(resolve_config, "r") as f:
            contents = f.readlines()
        nameservers = [
            line.split()[1] for line in contents if re.match(r"^\s*nameserver\s", line)
        ]
        if ipv4_only:
            nameservers = [n for n in nameservers if not re.search("[a-zA-Z]", n)]
        # De-duplicate the list of nameservers
        nameservers = list(set(nameservers))
    except FileNotFoundError:
        nameservers = []
    return nameservers


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
